# ADR-003 — Evidence-based RCA

**Status:** Accepted

RCA is deterministic/scored correlation over evidence. It is not delegated to the LLM.

Initial candidate score combines:

- anomaly strength;
- temporal precedence;
- dependency/trace relevance;
- log evidence;
- optional recent-change context.

RCA output is a ranked list of **suspected root causes**, not a claim of guaranteed causality. Every candidate must reference supporting evidence IDs.
