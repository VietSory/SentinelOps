# Phase 4 live capture — first attempt (FAIL)

Source capture commit: `25728c7`. Profile: `prometheus-increase-60s/v1`, 15s scrape, 60s buckets, 300s feature windows. Captures retained under ignored `artifacts/step4/2026-10-26-live`.

A real 450s workload exercised healthy, 1200ms Payment latency, Payment errors and recovery. The source captured 21 observations. The smoke correctly failed its feature-coverage requirement: only one eligible feature window was produced. Several buckets had only 3/4 scrape samples or a newly appearing HTTP status series; their 75% coverage was rejected rather than turned into healthy input.

The default SDK histogram also interpolated a ~1200ms injected delay near 2425ms because its next upper bound was 2500ms. This was distribution-resolution error, not a measured 2.4s application delay.

Follow-up: use a separately versioned 5s scrape profile, preserve decoding of old captures, add histogram resolution near 1200ms, extend the error phase to two full minutes, and poll the final exact trace until SDK export/indexing completes. Repeat the full live test and publish that result separately. No G1 PASS or model/AWS claim is made by this attempt.
