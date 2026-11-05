# Native trace timing and receipt clocks

The internal Jaeger v3 decoder now retains observed span start/end time and duration for evidence consumers. Previously it preserved parentage/status while discarding native timing, so a successful topology smoke could not establish temporal evidence readiness.

`span.timing` is either `null` for legacy payloads missing both timing fields, or an object with exact integer `start_unix_ns`, `end_unix_ns` and `duration_ns`. Missing timing is not a zero-duration span. Half-present, boolean, fractional, negative, overflowing and reversed timestamps fail parsing. Valid zero-duration spans remain distinguishable from missing fields.

The [OpenTelemetry span definition](https://github.com/open-telemetry/opentelemetry-proto/blob/main/opentelemetry/proto/trace/v1/trace.proto) specifies epoch nanoseconds and end time at or after start; the [OTLP JSON specification](https://github.com/open-telemetry/opentelemetry-proto/blob/main/docs/specification.md) permits numeric or decimal-string encoding of fixed64 values. Supported numeric inputs here are exact integers; floating-point timestamp decoding is refused to avoid losing nanoseconds. Values retain unsigned 64-bit bounds. No conversion through seconds, milliseconds or floating-point arithmetic is used to calculate duration.

`validate_trace(..., require_timing=True)` requires complete, consistent timing on every returned span in addition to existing trace identity, parentage, correlation and status checks. Real Step 3/4 smoke consumers enable it. Legacy topology/status callers can still use the default behavior, but cannot claim temporal readiness from that alone. Cross-host parent/child clock containment or temporal precedence is not asserted without clock uncertainty evidence.

The explicit `root_service=payment-service/inventory-service` profile validates a direct business request: one canonical HTTP server, matching status/correlation, exact native timing and any PostgreSQL client spans owned by that same process/server. A captured internal parent/cycle, foreign service, invalid route, missing timing or client outside its owning server fails. This profile always requires timing. The default Checkout validator still requires its complete three-service cascade.

Direct raw snapshots must carry `request_entry_service`, observed from the load request's actual destination. It is request-routing metadata, not a root-cause label. A partial Checkout trace without that explicit scope is never automatically reclassified as a direct request; conflicting captured completion logs also reject the claimed scope. RCA uses the separately versioned `native-direct-span-duration-share/v1` method for a direct request's own reference duration. Existing complete-cascade duration support and previous S0/S3 evidence/results remain unchanged apart from code provenance.

New Step 4 and experiment-runner trace/log snapshots record UTC `collected_at` after the complete response/log read. This is a receipt observation, not the event timestamp or proof of earliest backend availability. Step 4 evidence summaries preserve these clocks and show null for older snapshots without them; CI verifies receipt metadata on newly captured evidence. Existing immutable captures are not rewritten or backdated.

Retaining event time and recording new receipt clocks are prerequisites for RCA evidence, not a diagnosis or causal proof. Existing development snapshots lacking receipt clocks remain unsuitable for verified online cutoff/MTTD/lead-time claims. Later RCA consumers must check freshness/cutoff, source/clock uncertainty, missing modalities and evidence links separately; no model or remediation action is enabled by these fields.


## X-Ray adapter preparation

\`sentinelops.telemetry.xray.XRaySource\` captures one explicitly observed request
using an injected client. It has no default SDK client, credential discovery or
cloud call on import/construction. Configure bounded SDK connect/read/retry
settings, then call \`capture(trace_id, request_entry_service=...)\` only within
an authorized cloud scope. The original request-entry service must come from
observed routing metadata and is bound before the query; replay cannot replace
it or infer a direct request from a partial Checkout cascade.

The request uses \`BatchGetTraces\` and follows pagination, including empty pages.
Per-capture limits are 20 pages / 16 MiB, 64 KiB per native document, 4,096 spans
and depth 64. The account/environment are explicit operator context and the
client's configured region must match. This is not caller-account/IAM attestation;
H3 must verify actual source identity and deployment independently. API errors
retain only their class, not SDK exception text. Original document strings and
actual response-complete receipt clocks are preserved. Unprocessed IDs,
truncation, in-progress segments and unavailable parents cannot supply a
supported trace. A later capture is a new immutable object, not a silent repair.

X-Ray's formatted trace ID is mapped to W3C form by joining its 8- and 24-hex
groups. Its prefix is never used to infer an event clock. Native JSON numeric
seconds are parsed with Decimal and converted exactly to bounded integer
nanoseconds; boolean/string/nonfinite/reversed/overprecision values fail rather
than being rounded or turned into zero. This preserves the source number, not
nanosecond clock accuracy; AWS documents millisecond timing accuracy.

\`normalize_capture\` requires original CloudWatch completion captures from the
same declared account/region/environment. Those captures must pass the existing
pagination/query/clock/independent-count checks. Server trace/span/service IDs,
HTTP status, business route and correlation must agree with completion logs;
the log's millisecond interval must intersect its native server span. Inferred
segments without original server proof do not become observed server spans.
Native remote HTTP subsegments become client links only when their observed
parent IDs connect to a log-attested downstream server; endpoint/name aliases
are not guessed. Nested and independently sent subsegments keep native parents.
Duplicate IDs, cycles and a client outside its owning segment fail. Direct
scope also rejects a captured upstream parent even when that parent is unmapped;
ignoring unsupported nodes cannot turn a partial cascade into a direct request.

Existing \`validate_trace(require_timing=True)\` checks the supported Checkout
cascade or explicitly captured direct scope. Parent/child enclosure is checked
within the owning segment, not across hosts with unknown clock uncertainty.
Unmapped database/internal spans retain IDs and an explicit unsupported-role
reason; no RDS identity, SQL, URL, user or arbitrary metadata is copied into the
normalized evidence. Original private captures retain the full native source.

Replay without AWS credentials or network calls:

```sh
python -m scripts.replay_xray --store /trusted/source \
  --capture-ref artifact://raw/XRAY_CAPTURE_HASH.json \
  --completion-capture-ref artifact://raw/CLOUDWATCH_CAPTURE_HASH.json \
  --environment aws-development --correlation-id OBSERVED_CORRELATION_ID \
  --as-of UTC_CUTOFF --output /new/exclusive/replay
```

Repeat \`--completion-capture-ref\` for the original service/bucket captures.
The decoder bounds them to 1,000 captures and 64 MiB in total. Unavailable input
returns exit 2 without an output directory. A successful output archives the
original objects and a structural report; the maximum API/log receipt controls
availability and later receipts cannot enter an earlier cutoff. No earliest
backend-arrival clock is reconstructed.

The profile \`xray-log-attested-request/v1\` is adapter preparation: structural
validation is not full trace completeness, sampling coverage, timing parity,
RCA accuracy or action authority. Reports always keep \`source_parity=not-accepted\`,
\`actions_allowed=false\`, unknown clock uncertainty and explicit limitations.
There is no default worker/RCA wiring, model/source approval or live AWS result.
A real H3 capture still needs the deployed ADOT mapping, verified source/IAM,
backend capability, account and clock/receipt/sampling evidence. AWS notes that
\`BatchGetTraces\` cannot find traces when Transaction Search is enabled; such a
backend needs its appropriate separately validated adapter, not a guessed result.

Sources: [native segment fields and W3C formatting](https://docs.aws.amazon.com/xray/latest/devguide/xray-api-segmentdocuments.html),
[BatchGetTraces behavior](https://docs.aws.amazon.com/xray/latest/api/API_BatchGetTraces.html),
[ADOT integration](https://docs.aws.amazon.com/xray/latest/devguide/xray-services-adot.html).
