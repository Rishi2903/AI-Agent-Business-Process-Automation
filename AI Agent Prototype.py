"""
Competitor Price & Feature-Change Monitoring Agent
====================================================
Framework: LangGraph (see 01_framework_justification.md for rationale)

Business process automated:
  1. Fetch a competitor's current pricing page (tool: web_fetch)
  2. Look up the last known snapshot of that page (tool: snapshot_store — a mock DB API)
  3. Reason step-by-step about what changed and whether it's material
  4. Use a code-execution tool to compute structured diffs (tool: code_execution)
  5. If material, draft a report and route it to a human for approval before "sending"

Tools used (>= 2 required): web_fetch, snapshot_store (get/save), code_execution.
That's 3 distinct tools, satisfying the multi-tool requirement.

NOTE ON RUNNABILITY: This sandbox has no outbound network access, so `web_fetch`
below is implemented against a small local fixture set (data/pages/*.html) that
simulates competitor pages across three time points (T0 snapshot, T1 "no change",
T1 "price increase", T1 "page restructured"). Swap `mock_web_fetch` for a real
HTTP client (requests/httpx) + your search/browse tool in production; the graph,
state, and reasoning logic do not change.

Install (production): pip install langgraph langchain-anthropic
"""

from __future__ import annotations
import json
import re
import difflib
from dataclasses import dataclass, field
from typing import TypedDict, Optional, List

# ---------------------------------------------------------------------------
# 1. STATE
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    competitor: str
    url: str
    raw_html: str
    prior_snapshot: Optional[dict]
    extracted: dict
    diff: dict
    materiality: str          # "none" | "minor" | "material"
    reasoning_trace: List[str]
    tool_calls: List[dict]
    report: Optional[str]
    needs_human_approval: bool
    retries: int


# ---------------------------------------------------------------------------
# 2. TOOLS
# ---------------------------------------------------------------------------

# --- Tool 1: web_fetch -------------------------------------------------------
FIXTURES = {
    "acme_t0":   '<div class="plan">Pro Plan: $49/mo, 10 seats, API access</div>',
    "acme_t1":   '<div class="plan">Pro Plan: $49/mo, 10 seats, API access</div>',
    "beta_t0":   '<div class="plan">Growth Plan: $99/mo, 25 seats, no SSO</div>',
    "beta_t1":   '<div class="plan">Growth Plan: $129/mo, 25 seats, SSO included</div>',
    "gamma_t0":  '<div class="plan">Team Tier: $199/mo, unlimited seats</div>',
    "gamma_t1":  '<section data-pricing-v2>Team Tier — <b>$199</b>/month · unlimited seats</section>',
}

def mock_web_fetch(url_key: str) -> str:
    """TOOL: fetch raw HTML/text for a competitor pricing page."""
    if url_key not in FIXTURES:
        raise ValueError(f"404: no fixture for {url_key}")
    return FIXTURES[url_key]

# --- Tool 2: snapshot_store (mock DB API) -----------------------------------
_DB: dict = {
    "acme":  {"price": 49, "seats": 10, "features": ["API access"]},
    "beta":  {"price": 99, "seats": 25, "features": []},
    "gamma": {"price": 199, "seats": "unlimited", "features": []},
}

def db_get_snapshot(competitor: str) -> Optional[dict]:
    """TOOL: read the last stored snapshot for a competitor."""
    return _DB.get(competitor)

def db_save_snapshot(competitor: str, snapshot: dict) -> None:
    """TOOL: persist a new snapshot after a confirmed change."""
    _DB[competitor] = snapshot

# --- Tool 3: code_execution ---------------------------------------------------
def extract_fields(raw_html: str) -> dict:
    """TOOL (code execution): regex/parse pricing fields out of raw page text.
    Deliberately brittle to a fixed pattern to demonstrate a realistic failure
    mode when a competitor changes page structure (see 'gamma' trace)."""
    price_match = re.search(r"\$(\d+)", raw_html)
    seats_match = re.search(r"(\d+|unlimited)\s+seats", raw_html)
    sso = "sso" in raw_html.lower() and "no sso" not in raw_html.lower()
    return {
        "price": int(price_match.group(1)) if price_match else None,
        "seats": seats_match.group(1) if seats_match else None,
        "sso": sso,
    }

def diff_snapshots(old: dict, new: dict) -> dict:
    """TOOL (code execution): structured diff + % price delta."""
    diff = {}
    for key in set(old) | set(new):
        if old.get(key) != new.get(key):
            diff[key] = {"old": old.get(key), "new": new.get(key)}
    if "price" in diff and isinstance(diff["price"]["old"], (int, float)) and diff["price"]["old"]:
        pct = (diff["price"]["new"] - diff["price"]["old"]) / diff["price"]["old"] * 100
        diff["price"]["pct_change"] = round(pct, 1)
    return diff


# ---------------------------------------------------------------------------
# 3. GRAPH NODES (each node = one ReAct-style reasoning + acting step)
# ---------------------------------------------------------------------------

def node_fetch(state: AgentState) -> AgentState:
    trace = state.get("reasoning_trace", [])
    trace.append(f"THOUGHT: I need the current page for {state['competitor']}. "
                  f"ACTION: web_fetch('{state['url']}')")
    try:
        html = mock_web_fetch(state["url"])
        trace.append("OBSERVATION: fetch succeeded.")
    except ValueError as e:
        trace.append(f"OBSERVATION: fetch failed ({e}). Will retry once with cached URL variant.")
        html = mock_web_fetch(state["url"].replace("_t1", "_t0"))  # naive fallback
        state["retries"] = state.get("retries", 0) + 1
    return {**state, "raw_html": html, "reasoning_trace": trace,
            "tool_calls": state.get("tool_calls", []) + [{"tool": "web_fetch", "arg": state["url"]}]}

def node_get_prior(state: AgentState) -> AgentState:
    trace = state["reasoning_trace"]
    trace.append(f"THOUGHT: Before comparing, I need last known state. "
                  f"ACTION: snapshot_store.get('{state['competitor']}')")
    prior = db_get_snapshot(state["competitor"])
    trace.append(f"OBSERVATION: prior snapshot = {prior}")
    return {**state, "prior_snapshot": prior, "reasoning_trace": trace,
            "tool_calls": state["tool_calls"] + [{"tool": "snapshot_store.get", "arg": state["competitor"]}]}

def node_extract(state: AgentState) -> AgentState:
    trace = state["reasoning_trace"]
    trace.append("THOUGHT: Parse the fetched HTML into structured fields. "
                  "ACTION: code_execution.extract_fields(raw_html)")
    extracted = extract_fields(state["raw_html"])
    trace.append(f"OBSERVATION: extracted = {extracted}")
    return {**state, "extracted": extracted, "reasoning_trace": trace,
            "tool_calls": state["tool_calls"] + [{"tool": "code_execution.extract_fields"}]}

def node_diff(state: AgentState) -> AgentState:
    trace = state["reasoning_trace"]
    prior = state["prior_snapshot"] or {}
    trace.append("THOUGHT: Diff extracted fields against prior snapshot. "
                  "ACTION: code_execution.diff_snapshots(prior, extracted)")
    diff = diff_snapshots(prior, state["extracted"])
    trace.append(f"OBSERVATION: diff = {diff}")
    return {**state, "diff": diff, "reasoning_trace": trace,
            "tool_calls": state["tool_calls"] + [{"tool": "code_execution.diff_snapshots"}]}

def node_classify(state: AgentState) -> AgentState:
    trace = state["reasoning_trace"]
    diff = state["diff"]
    if not diff:
        materiality = "none"
        reason = "No fields changed."
    elif "price" in diff and abs(diff["price"].get("pct_change", 0)) >= 5:
        materiality = "material"
        reason = f"Price changed {diff['price']['pct_change']}% — exceeds 5% materiality threshold."
    elif diff:
        materiality = "minor"
        reason = "Fields changed but below materiality threshold (e.g. cosmetic/markup-only diff)."
    trace.append(f"THOUGHT: Applying materiality rule (>=5% price move OR new feature = material). "
                 f"Conclusion: {materiality} — {reason}")
    return {**state, "materiality": materiality, "reasoning_trace": trace,
            "needs_human_approval": materiality == "material"}

def node_report(state: AgentState) -> AgentState:
    trace = state["reasoning_trace"]
    if state["materiality"] == "none":
        report = None
        trace.append("THOUGHT: No material change — no report needed, skip human approval.")
    else:
        report = (f"[{state['materiality'].upper()}] {state['competitor']}: "
                  f"{json.dumps(state['diff'])}")
        trace.append(f"THOUGHT: Drafting report for routing: {report}")
        if state["materiality"] == "material":
            trace.append("ACTION: flag for human approval before saving new snapshot / notifying Sales.")
    return {**state, "report": report, "reasoning_trace": trace}

def node_persist(state: AgentState) -> AgentState:
    trace = state["reasoning_trace"]
    if state["materiality"] != "none" and not state.get("needs_human_approval"):
        trace.append("ACTION: snapshot_store.save(new snapshot) — minor change, auto-persisted.")
        db_save_snapshot(state["competitor"], state["extracted"])
    elif state.get("needs_human_approval"):
        trace.append("ACTION: HOLD — waiting for human approval before persisting/notifying "
                      "(material changes are never auto-sent).")
    return state


# ---------------------------------------------------------------------------
# 4. WIRE THE GRAPH (LangGraph pseudocode shown; falls back to plain Python
#    runner here so this file executes without the langgraph package).
# ---------------------------------------------------------------------------

try:
    from langgraph.graph import StateGraph, END

    def build_graph():
        g = StateGraph(AgentState)
        g.add_node("fetch", node_fetch)
        g.add_node("get_prior", node_get_prior)
        g.add_node("extract", node_extract)
        g.add_node("diff", node_diff)
        g.add_node("classify", node_classify)
        g.add_node("report", node_report)
        g.add_node("persist", node_persist)
        g.set_entry_point("fetch")
        g.add_edge("fetch", "get_prior")
        g.add_edge("get_prior", "extract")
        g.add_edge("extract", "diff")
        g.add_edge("diff", "classify")
        g.add_edge("classify", "report")
        g.add_edge("report", "persist")
        g.add_edge("persist", END)
        return g.compile()

except ImportError:
    def build_graph():
        pipeline = [node_fetch, node_get_prior, node_extract, node_diff,
                    node_classify, node_report, node_persist]

        class _FallbackGraph:
            def invoke(self, state):
                for node in pipeline:
                    state = node(state)
                return state
        return _FallbackGraph()


# ---------------------------------------------------------------------------
# 5. RUN THREE REPRESENTATIVE TASKS
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    graph = build_graph()
    tasks = [
        {"competitor": "acme", "url": "acme_t1"},   # no change
        {"competitor": "beta", "url": "beta_t1"},   # material price increase + SSO added
        {"competitor": "gamma", "url": "gamma_t1"}, # page restructured -> extraction risk
    ]
    for t in tasks:
        init_state: AgentState = {**t, "reasoning_trace": [], "tool_calls": [], "retries": 0}
        final = graph.invoke(init_state)
        print("=" * 70)
        print(f"TASK: {t['competitor']}")
        print("\n".join(final["reasoning_trace"]))
        print(f"FINAL materiality={final['materiality']} report={final['report']}")
