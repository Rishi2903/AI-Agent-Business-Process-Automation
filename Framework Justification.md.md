# Framework Selection Justification (300 words)

**Chosen process:** Competitor price & feature-change monitoring for a B2B SaaS pricing team. Today an analyst manually revisits ~10 competitor pricing pages weekly, eyeballs what changed, decides whether it's material, and emails a summary to Sales/Product. It's multi-step, requires external lookup, and requires judgment about materiality — a strong fit for agentic automation with a human still approving the final alert.

**Frameworks compared:** LangGraph, AutoGen, CrewAI.

- **AutoGen** models the task as a conversation between agents (e.g., a "Researcher" and a "Critic" talking to each other). This is powerful for open-ended, debate-style problems, but our pipeline is linear (fetch → extract → diff → classify → report), and conversational orchestration adds non-determinism and token overhead without adding value. Tool-calling in AutoGen is also less tightly coupled to explicit state than we want for an auditable business process.
- **CrewAI** offers a fast, role-based abstraction ("Scraper agent," "Analyst agent") that is easy to prototype but currently gives less fine-grained control over conditional branching (e.g., "if extraction confidence is low, retry with a different parser before escalating to a human") and weaker built-in state persistence/checkpointing for production use.
- **LangGraph** represents the workflow as an explicit state graph: each node is a discrete step (tool call or reasoning step), edges encode conditional logic, and state is a typed, inspectable object. This gives us (a) deterministic control over tool sequencing, which matters when the output feeds a business report, (b) native checkpointing, so a failed run can resume rather than re-billing every step, (c) first-class support for a human-approval node before anything is sent externally, and (d) trace/observability tooling (LangSmith) that makes the ReAct trace inspectable — required for Step 3 of this assignment.

**Decision:** LangGraph, because the process is a controllable pipeline with a compliance-relevant output, not an open-ended multi-agent negotiation.
