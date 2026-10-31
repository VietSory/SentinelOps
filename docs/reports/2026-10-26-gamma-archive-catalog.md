# Complete GAMMA v1 archive catalogue

Clean implementation commit `7d21b59f0e88e6fbd2800b9f6fb18fb979d7d327` inventoried the publisher's canonical version-pinned ZIP64 archive using [bounded HTTP ranges](../archive/guides/GAMMA_ARCHIVE_ACQUISITION.md). This verifies the complete **member inventory**, not all member contents, source labels, training splits or live compatibility.

- Archive representation size: **10,111,803,053 bytes**.
- Central directory starts at byte **10,090,375,449**.
- **129,510 entries**; no classic ZIP entry-count truncation.
- **Two archive range responses, 21,493,042 body bytes**, plus separate bounded publisher metadata requests. This is transferred response-body content, not a network billing estimate.
- **190 processed experiment names; 182 have all three processed request types.** The catalogue contains 559 processed files, 85,872 raw metric files, 6,484 pod-description files and other original modalities. Completeness of a filename inventory does not prove independent experiments or complete request sampling.

The immutable canonical inventory is `data/raw/public/inventories/69934c78fb790eeaa12931f25669770b50e44c70b647fd98c8a6d63698b4caf8.json`. The [small project catalogue lock](../../configs/gamma_v1_archive_catalog.json) freezes this SHA256 and source/policy/archive identity. A second real acquisition from clean commit `dfc7b8e0ef24d3d0dd3ecd0500dbf5d3fe04c3b5` passed that exact lock and reproduced the identical catalogue hash, 129,510 entries and 21,493,042 archive body bytes. The [measured report](evidence/2026-10-26-gamma-archive-catalog.json) records exact counts and modality distribution. Publisher version, declared license and metadata remained unchanged across inventory/replay.

## What this enables

Metadata-only scope analysis finds **194 raw experiment directories with `args.txt`**. Four lack any processed table; none of the 190 processed experiment names lacks raw args. A future raw-resource cohort can therefore be selected independently of processed label availability, using all raw experiments with args rather than choosing cases based on scores.

For that candidate scope, catalogue sizes indicate:

| Original role | Files | Expanded bytes |
| --- | ---: | ---: |
| Four required raw resource metrics | 23,048 | 68,196,044 |
| Per-experiment pod placement descriptions | 6,484 | 18,334,720 |
| Native phase logs | 634 | 194,033 |
| Experiment args | 194 | 105,144 |
| Total candidate raw files | 30,360 | 86,829,941 |

Their ZIP member payloads total 20,778,664 compressed bytes before local headers, metadata ranges and optional coalescing overhead. These are catalogue estimates, not completed acquisition counts. Global metadata would be additional. At least one directory has only args, eleven have no selected phase file, some have multiple pod descriptions per entity, and later experiments use CPU/memory-specific phase filename prefixes. Those cases require explicit semantic admission/quarantine; file count alone cannot make them training-ready.

The acquisition implementation must freeze its exact role/case scope before extraction, preserve native per-experiment placement and source bytes, bound range/member sizes, check local ZIP headers/CRC, freeze the resulting per-file SHA256 tree, and verify observed capture/session grouping before windowing/splitting. Missing phase logs must not silently label an experiment healthy. The earlier three-file label parser is scoped to its original CPU experiment and is not yet certified for the later naming/placement variants.

## Validation

The final full project suite passed **248 tests**, including all 13 archive tests. Installed-wheel module/schema/API smoke passed outside the checkout; 19 schemas, 14 samples and the actual frozen policy configuration validate. Tests cover strict range responses, byte budgets, representation changes, seekable member reads, corrupt ZIP CRC, unsafe/noncanonical paths and a generated 65,536-entry ZIP64 catalogue. CI now reproduces the entire real member inventory against the project lock in addition to the 142-file feature/label replay. This report does not claim corpus-wide content validation or G2/G3/G7 completion.
