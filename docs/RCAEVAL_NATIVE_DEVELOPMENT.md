# Native development evidence adapter

The default protocol freezes the three previously inspected development cases in
`configs/rcaeval_development_evidence.json`. Acquisition uses the pinned publisher
manifest and existing role/hash guards. The evaluator rejects validation/holdout
before opening raw files. No provider scripts, fill/backfill or source relabelling
is used. Native services retain their publisher identities.

Diagnostic input is metric timestamps/channel identities, native logs and native
span records. Evaluator case/root/fault/injection metadata is joined **after**
diagnostic scoring. The adapter observes parent-service edges but does not claim
that unknown native duration/span-kind/receipt semantics satisfy the local trace
profile. Native metric values are not a compatible IF score or health certificate.
It does not construct false HTTP SLO episodes for a foreign system.

The initial supported native component is explicitly named error-token presence
in captured native logs, under `native-error-tokens/v1`. Fixed tokens are error,
exception, failed, failure, panic and fatal. This is bounded positive log support,
not an unsampled log denominator, causality claim or absence-of-error health claim.
Only observed in-range integer timestamps/messages contribute. Missing service logs
remain unavailable. Original receipt clocks remain unknown.

The source-neutral kernel reuses the existing RCA `_score`/abstention logic and
unchanged weights/floors; the new native log method is supplemental. The existing
strict local `rca-result` contract is unchanged and cannot carry native method
outputs. Native development results use `native-rca-development/v1` in evaluation
artifacts, never a fabricated local incident or changed IER. At most the 0.15 log
weight is available, so every case must abstain under the frozen 0.75 floors.

Report top-k/MRR over all three selected single-root cases, including missing and
abstained results, alongside per-case coverage and 100% expected abstention.
These are descriptive development rankings, not validated diagnostic accuracy or
independent trial estimates. There is no confidence interval from three unproven
independent sessions. Native log support versus removing that actual evidence is
the supported ablation; unavailable ML/traces/topology are not scored as real data.
Local cascading/independent-failure evaluation remains a separate runtime protocol.

```sh
python -m scripts.evaluate_rcaeval --manifest data/raw/public/manifests/HASH.json \
  --output artifacts/phases/rcaeval-development.json
```

The CLI requires a committed protocol/code tree before scoring. Source acquisition,
record counts, native hypothesis ranking and final heldout performance remain
distinct evidence claims. No threshold is tuned from these results.
