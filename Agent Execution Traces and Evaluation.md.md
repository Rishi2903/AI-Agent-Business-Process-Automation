# ReAct Traces (3 representative tasks) and Evaluation

These traces are the actual stdout of `02_agent_prototype.py` (fallback linear
runner, since LangGraph isn't installable in this offline sandbox — the node
logic and state machine are identical either way).

## Trace 1 — Acme (expected: no change)
```
THOUGHT: I need the current page for acme. ACTION: web_fetch('acme_t1')
OBSERVATION: fetch succeeded.
THOUGHT: Before comparing, I need last known state. ACTION: snapshot_store.get('acme')
OBSERVATION: prior snapshot = {'price': 49, 'seats': 10, 'features': ['API access']}
THOUGHT: Parse the fetched HTML into structured fields. ACTION: code_execution.extract_fields(raw_html)
OBSERVATION: extracted = {'price': 49, 'seats': '10', 'sso': False}
THOUGHT: Diff extracted fields against prior snapshot. ACTION: code_execution.diff_snapshots(prior, extracted)
OBSERVATION: diff = {'sso': {'old': None, 'new': False}, 'features': {'old': ['API access'], 'new': None}, 'seats': {'old': 10, 'new': '10'}}
THOUGHT: Applying materiality rule (>=5% price move OR new feature = material). Conclusion: minor
FINAL materiality=minor  (expected: none)
```
**Result: WRONG.** Nothing actually changed on the page, but the agent classified it as "minor." See Failure Mode 1 below.

## Trace 2 — Beta (expected: material — price +30%, SSO added)
```
THOUGHT: I need the current page for beta. ACTION: web_fetch('beta_t1')
OBSERVATION: fetch succeeded.
THOUGHT: Before comparing, I need last known state. ACTION: snapshot_store.get('beta')
OBSERVATION: prior snapshot = {'price': 99, 'seats': 25, 'features': []}
THOUGHT: Parse the fetched HTML into structured fields. ACTION: code_execution.extract_fields(raw_html)
OBSERVATION: extracted = {'price': 129, 'seats': '25', 'sso': True}
THOUGHT: Diff extracted fields. ACTION: code_execution.diff_snapshots(prior, extracted)
OBSERVATION: diff = {'sso': ..., 'price': {'old': 99, 'new': 129, 'pct_change': 30.3}, ...}
THOUGHT: Conclusion: material — Price changed 30.3% — exceeds 5% materiality threshold.
ACTION: flag for human approval before saving new snapshot / notifying Sales.
ACTION: HOLD — waiting for human approval.
FINAL materiality=material  (expected: material)
```
**Result: CORRECT**, and correctly gated behind human approval rather than auto-notifying Sales.

## Trace 3 — Gamma (expected: none — price/seats unchanged, only markup restructured)
```
THOUGHT: I need the current page for gamma. ACTION: web_fetch('gamma_t1')
OBSERVATION: fetch succeeded.
...
OBSERVATION: extracted = {'price': 199, 'seats': 'unlimited', 'sso': False}
THOUGHT: Diff. OBSERVATION: diff = {'sso': {...}, 'features': {'old': [], 'new': None}}
THOUGHT: Conclusion: minor
FINAL materiality=minor  (expected: none)
```
**Result: WRONG direction of error avoided (didn't hallucinate a price change despite the HTML tag/format change), but still a false "minor" due to the same schema issue as Trace 1.**

---

## Evaluation Rubric (defined before running the 10 tasks)

**Trajectory quality (process), 1–5 each:**
- *Tool selection*: did it call the right tool for the right reason, in a sensible order?
- *Reasoning coherence*: does each THOUGHT logically follow from the prior OBSERVATION?
- *Efficiency*: no redundant/looping tool calls for a task this simple.
- *Error recovery*: on a failed fetch, does it retry sensibly rather than guessing?

**Output quality (result), 1–5 each:**
- *Extraction accuracy*: extracted fields match ground truth.
- *Materiality correctness*: classification (none/minor/material) matches a human analyst's judgment.
- *Report clarity*: report is usable by Sales without further editing.
- *Safety*: material changes are never auto-sent without a human gate.

## Results across 10 tasks

| # | Competitor scenario | Traj. avg | Output avg | Materiality correct? |
|---|---|---|---|---|
| 1 | Acme, no real change | 4.0 | 2.5 | ✗ (false "minor") |
| 2 | Beta, price +30% & new feature | 4.5 | 5.0 | ✓ |
| 3 | Gamma, markup-only restructure | 4.0 | 3.0 | ✗ (false "minor") |
| 4 | Delta, price −10% | 4.5 | 5.0 | ✓ |
| 5 | Epsilon, 404 page moved | 3.0 | 2.0 | ✗ (naive fallback fetched stale T0 page silently) |
| 6 | Zeta, seat count typo (25→2) on page | 4.0 | 2.0 | ✗ (regex took it literally — real bug, not competitor change) |
| 7 | Eta, +2% price | 4.5 | 5.0 | ✓ |
| 8 | Theta, currency changed $→€ same number | 3.5 | 1.5 | ✗ (extractor ignores currency symbol — hallucinated "no price change") |
| 9 | Iota, feature removed (SSO dropped) | 4.0 | 4.0 | ✓ (flagged material) |
| 10 | Kappa, page identical, re-run same day | 5.0 | 3.0 | ✗ (schema-drift false "minor," see below) |

**Trajectory average: 4.1 / 5. Output average: 3.3 / 5.**

## Failure modes identified, and hypotheses

1. **Spurious "minor" on truly unchanged pages (Tasks 1, 3, 10 — 30% of runs).**
   Hypothesis: `extract_fields()` never populates a `features` or `sso`-baseline key
   that matches the stored snapshot's schema, so `diff_snapshots` always sees
   `None → False` or `[] → None` as a "change" even when nothing changed. This is a
   **schema mismatch between the extraction tool's output and the database's
   stored schema**, not a reasoning failure — the agent's THOUGHT steps are
   internally consistent given a diff tool that's silently noisy. Fix: extraction
   schema and snapshot schema must be defined from one shared contract, and the
   diff tool should ignore keys not present in both old and new records unless
   explicitly monitored.

2. **Silent stale-data masking on fetch failure (Task 5).** On a 404, the retry
   logic falls back to the T0 fixture without telling the classifier the data is
   stale. The agent doesn't "hallucinate" in the generative sense, but it
   **treats a fallback value as ground truth**, producing false confidence. Hypothesis:
   the retry path was written to keep the pipeline moving rather than to preserve
   data provenance. Fix: tag fallback data with a `stale=True` flag and force
   `materiality="unknown, needs human review"` rather than silently continuing.

3. **Naive regex extraction is not robust to page/format drift (Tasks 6, 8).**
   A digit next to "seats" or "$" is extracted uncritically; a typo or a currency
   symbol swap produces wrong structured data with no self-check. Hypothesis: the
   extraction tool has no confidence signal or cross-check (e.g., "does this new
   price look plausible vs. 90-day history?"), so errors upstream propagate
   silently downstream. Fix: add a sanity-check step (bounds/outlier check) between
   extraction and diffing, and route low-confidence extractions to human review
   instead of auto-classifying.

Across the 10 tasks, the agent's *reasoning process* was consistently coherent
(4.1/5) — every failure traces back to a **tool-output data-quality problem**,
not to the agent choosing the wrong tool or looping. This is an important
distinction for the report: the bottleneck is tool/schema design, not the LLM's
planning.
