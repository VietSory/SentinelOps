# ADR-003 — Evidence-based RCA

**Status:** Accepted

RCA candidate ranking remains deterministic/scored correlation over evidence. An optional LLM may synthesize a human-readable explanation for a ranked candidate from its supporting evidence; it cannot add candidates, change scores or claim confirmation. Without a configured provider or on provider failure, the deterministic hypothesis renderer remains available.

Initial candidate score combines:

- anomaly strength;
- temporal precedence;
- dependency/trace relevance;
- log evidence;
- optional recent-change context.

RCA output is a ranked list of **suspected root causes**, not a claim of guaranteed causality. Every candidate must reference supporting evidence IDs.
