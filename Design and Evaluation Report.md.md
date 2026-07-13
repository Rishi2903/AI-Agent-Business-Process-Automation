# Design and Evaluation Report
### Competitor Price & Feature-Change Monitoring Agent

## 1. The business process

A SaaS pricing/competitive-intelligence analyst currently spends several hours
a week revisiting a fixed list of competitor pricing pages, manually noting
what changed since the last check, judging whether the change is worth
escalating, and writing a short email to Sales and Product when it is. The
process is genuinely suited to agentic automation for three reasons that the
assignment brief asks us to justify: it is **multi-step** (fetch → extract →
compare → judge → report → route), it **requires external information
lookup** that changes on an unpredictable schedule (competitor pages), and it
currently **requires human coordination and judgment** — specifically, a
materiality call ("is this worth Sales' attention?") that is exactly the kind
of bounded, rule-informed judgment an LLM agent can support, provided a human
still signs off before anything material goes out. That last point shaped the
whole design: the agent is built to *reduce the analyst's search-and-compare
burden*, not to remove their approval authority.

## 2. Framework choice

I compared LangGraph, AutoGen, and CrewAI (full justification in the
300-word deliverable) and selected **LangGraph**. The deciding factor is that
this process is a linear-but-conditional *pipeline* with a compliance-relevant
output (an external-facing business report), not an open-ended negotiation
between agents. AutoGen's conversational multi-agent pattern is well suited to
debate-style reasoning but adds non-determinism we don't want when the output
feeds a report a human will act on. CrewAI's role-based abstraction is fast to
prototype but, at the time of comparison, gave less explicit control over
conditional branching and state persistence than we needed for a
human-in-the-loop gate. LangGraph's explicit `StateGraph` — typed state,
inspectable nodes, conditional edges, native checkpointing — matches a
business process that must be auditable: every tool call and every reasoning
step needs to be traceable back to a specific state transition, both for
debugging and because a pricing report is the kind of artifact a compliance or
sales-ops team may later ask "why did the agent say this?" about.

## 3. Architecture

The agent is implemented as a seven-node graph: `fetch → get_prior → extract →
diff → classify → report → persist`. Three distinct tools are used:

1. **`web_fetch`** — retrieves the current competitor page (in production, a
   real HTTP client or a browsing tool; in this offline prototype, a fixture
   set standing in for live pages across two time points).
2. **`snapshot_store`** (`get`/`save`) — a database-style API holding the last
   known structured state per competitor, so the agent is comparing against
   ground truth rather than re-deriving history from scratch every run.
3. **`code_execution`** (`extract_fields`, `diff_snapshots`) — parses raw page
   content into structured fields (price, seats, SSO) and computes a
   structured diff with a percentage price delta.

Each node emits an explicit `THOUGHT` (why it's about to act),
an `ACTION` (which tool, with what argument), and an `OBSERVATION` (the tool's
result) — a direct implementation of the ReAct pattern (Yao et al., 2023),
which is what makes the trace legible for Step 3 of the assignment and for a
future auditor. Materiality is decided by an explicit, inspectable rule (≥5%
price move or a feature addition/removal = material) rather than an
unconstrained LLM judgment call, deliberately trading some flexibility for
predictability in a business-reporting context. Crucially, **no material
change is ever auto-persisted or auto-sent**: the `report`/`persist` nodes
route material findings to a `needs_human_approval` flag and stop, while minor
or no-change results are auto-logged. This human gate is the single most
important design decision in the system — it turns the agent from "an
automated decision-maker" into "an automated first draft," which matches how
the analyst actually wants to use it.

## 4. Multi-step reasoning demonstrated

The three captured traces (full text in the traces file) show the agent
reasoning across five distinct decisions per run: what to fetch, how to
compare it, how to structure the diff, how to classify materiality, and
whether to escalate to a human. In Trace 2 (Beta competitor), the agent
correctly identifies a 30.3% price increase paired with a newly added SSO
feature, classifies it as material, and — rather than sending anything —
explicitly holds for human approval. This is the clearest positive case: the
reasoning chain is coherent, the tool sequence is efficient (no redundant
calls), and the safety gate fires correctly.

## 5. Evaluation methodology and results

I defined the rubric *before* running the 10 tasks (per the assignment
instructions), scoring **trajectory quality** (tool selection, reasoning
coherence, efficiency, error recovery) and **output quality** (extraction
accuracy, materiality correctness, report clarity, safety) on 1–5 scales. Across
10 representative tasks — spanning no-change pages, real price/feature
changes, a moved/404 page, a data-entry typo, and a currency-symbol swap — the
agent averaged **4.1/5 on trajectory quality** but only **3.3/5 on output
quality**, and got the materiality classification wrong in 4 of 10 runs.

That gap is the most important finding in this project: **the agent's
reasoning process was consistently sound — it never looped, never called an
irrelevant tool, and its THOUGHT steps were always a logical consequence of
the prior OBSERVATION — but it was repeatedly let down by tool-output data
quality.** Three concrete failure modes emerged, each with a distinct
hypothesis:

- **Schema mismatch between extraction and storage** caused false "minor"
  classifications on genuinely unchanged pages (3 of 10 runs), because the
  extractor never populates the same keys the snapshot store expects, so the
  diff tool sees `None → False` as a change. This is a contract problem
  between two tools, not a planning failure.
- **Silent staleness masking**: on a 404, the fetch tool's retry fallback
  returned an old fixture without flagging it as stale, so a data-availability
  problem was invisible to the classifier downstream — a case where graceful
  degradation was implemented at the expense of provenance.
- **Unguarded extraction**: naive regex extraction had no plausibility check,
  so a page typo and a currency-symbol change both produced structurally
  valid but semantically wrong extracted data, which the diff tool then
  treated as truth.

None of these are hallucinations in the generative-text sense; they are what
I'd characterise as **tool-chain data-integrity failures that an LLM agent
inherits uncritically from an unguarded deterministic tool**. This distinction
matters for where engineering effort should go next: better prompting will not
fix this, because the reasoning steps that route around bad tool output are
already correct given the (wrong) observation they were handed.

## 6. Production deployment considerations

Three changes would be required before this moved beyond a prototype. First,
**tool contracts need shared schemas** (e.g., a Pydantic model both the
extractor and the snapshot store validate against) so silent key mismatches
become hard errors instead of spurious diffs. Second, **every tool observation
needs a provenance/confidence tag** — stale, low-confidence, or
out-of-bounds data should force a `needs_human_review` state rather than
flowing into automatic classification, extending the human-approval pattern
already used for material changes down into the extraction layer itself.
Third, at production scale, **rate limiting, caching, and ToS/legal review of
scraping targets** become operational requirements, alongside observability
(LangSmith-style tracing) so a pricing-ops team can audit any single alert
back to its raw tool calls — the same traceability that made this evaluation
possible should be a first-class production feature, not a debugging
convenience. The core conclusion from this evaluation is that the LangGraph
architecture and ReAct-style reasoning are already reliable; the investment
required to reach production is in the tools' data quality and self-checking,
not in the agent's control flow.

## 7. Limitations of this evaluation

Two limitations are worth flagging honestly. First, this prototype runs
against a fixed fixture set rather than live pages, because the development
sandbox had no outbound network access; the graph, tools, and reasoning logic
are unchanged by that substitution, but real competitor pages will introduce
failure modes this fixture set cannot — JavaScript-rendered pricing widgets,
paywalled tiers, A/B-tested pages showing different content per visitor, and
anti-scraping measures. The 10-task evaluation should therefore be read as a
lower bound on the failure rate the agent would see in production, not an
upper bound. Second, the materiality rubric (±5% price move, feature
add/remove) was set by me rather than validated against a real pricing
analyst's judgment; part of any pilot deployment would be running the agent
in shadow mode alongside the human process for several weeks and tuning the
threshold against cases where the analyst and the agent disagree, rather than
assuming the initial rule is correct.

## 8. Why keep a human in the loop at all

It would be tempting, given a 4.1/5 trajectory score, to let the agent
auto-send material alerts once the extraction issues above are fixed. I don't
think that's the right target state, for a reason distinct from the
data-quality failures found here: materiality is ultimately a business
judgment about what Sales and Product will find useful, and that judgment
shifts with context an agent has no visibility into (an active renewal
negotiation with a customer who mentioned this exact competitor, a pricing
change the team is already aware of, a page change that's cosmetic in one
quarter and strategically significant in the next). The value of this agent
is compressing a research task from "read ten pages and remember what they
looked like last month" down to "review one structured diff and a materiality
recommendation," not removing the analyst's judgment from the loop entirely.
That framing should carry through to any production rollout: success is
measured by analyst time saved and by the agent's false-negative rate (missed
material changes) approaching zero, even if that means tolerating a higher
false-positive rate that a human filters out in seconds.

## 9. Summary

The prototype demonstrates a working, ReAct-style, multi-tool LangGraph agent
that automates the mechanical steps of competitor monitoring — fetching,
extracting, diffing, and drafting — while keeping the materiality judgment
visible and gated behind human approval. The evaluation's central finding is
that the agent's *reasoning* is already reliable (4.1/5 trajectory quality
across 10 tasks); the work remaining before production is almost entirely in
hardening the tools it reasons over — shared schemas, provenance tagging on
stale or low-confidence data, and plausibility checks on extracted values —
rather than in the agent's planning or orchestration logic.

*(Word count: ~1,500)*
