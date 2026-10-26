# Public training data — selection and execution plan

This replaces the local-primary research-data assumption under [ADR-010](adr/ADR-010-public-training-data-and-transfer.md). Public datasets are the main development/training/evaluation direction; local measured experiments remain integration and transfer evidence. The existing 57-row local pilot is preserved and is not the training corpus for this workstream.

## Source selection

| Source | Role | Audited scope / limitation |
| --- | --- | --- |
| [RCAEval](https://huggingface.co/datasets/phamquiluan/RCAEval) | Primary microservice/RCA corpus | Publisher index contains 735 cases; metrics, root-cause service and injection time; modality availability differs by subset. Controlled faults, not a blanket production-data claim. |
| [SMD](https://github.com/NetManAIOps/OmniAnomaly) | IF/statistical anomaly benchmark | Original publisher describes 28 servers and 38 channels. Start with first lexicographic machine, selected before model scoring; original train/test boundary retained. Channels have no verified live RED mapping. |
| [Microsoft OpenRCA](https://github.com/microsoft/OpenRCA) | Candidate independent RCA evaluation | Heterogeneous enterprise telemetry/query benchmark. Dataset terms must be checked separately from repository MIT code license; full data is large. No bulk download or model claim yet. |
| [GAIA](https://github.com/CloudWise-OpenSource/GAIA-DataSet) | Candidate secondary multimodal corpus | Publisher describes two weeks of MicroSS metrics/logs/traces with simulated anomalies. It is another external controlled benchmark, not proof of production incidents. |

Popularity is a discovery signal; selection requires original publisher, provenance, usable labels, schema/units, licensing declaration and fit to the actual model task. Generic equipment-failure/network-attack tables cannot silently replace service telemetry.

## Pinned acquisition and audit

`configs/public_datasets.json` pins original source revisions. `scripts/download_public_data.py` fetches plain Parquet/text data, verifies Hugging Face LFS SHA256 or Git object hashes plus exact sizes, enforces download budgets and writes an immutable content-addressed provenance manifest. Existing corrupt files fail instead of being silently replaced. Raw data remains under ignored `data/raw/public/`.

```bash
python3.11 -m venv .venv-ml
.venv-ml/bin/python -m pip install -e '.[ml,dev]'
.venv-ml/bin/python scripts/download_public_data.py --dataset rcaeval
.venv-ml/bin/python scripts/download_public_data.py --dataset smd --machine machine-1-1
.venv-ml/bin/python scripts/audit_public_data.py --dataset rcaeval \
  --manifest data/raw/public/manifests/DOWNLOAD_HASH.json \
  --output artifacts/research/public-datasets/rcaeval-audit.json
```

The manifest path is printed by the downloader. Audit first verifies every file again, then reads tables without running remote code. Missing cells stay missing. Directory/case/root-cause names and injection labels stay outside feature vectors. The upstream data card's demonstration fill strategy is not adopted; future values cannot repair past windows.

The first real audit downloaded three representative RCAEval cases (one per suite, Online Boutique) and SMD machine-1-1. RCAEval's index, not just a high-level description, shows that RE2/RE3 Sock Shop have no traces and one RE2 Train Ticket case has no logs. Such gaps must be represented explicitly.

## Training and evaluation sequence

1. Commit source catalog and capability audit, including actual dimensions, missingness and label definitions.
2. Run the frozen `configs/public_smd_benchmark.json` protocol: first 70% of official train for fitting, last 30% for calibration; official test reserved for evaluation. Compare median/MAD with IF using the same calibration exceedance budget, no test threshold search and no point-adjustment inflation.
3. Expand anomaly evidence across the remaining SMD machines before any broad benchmark claim. Fit each machine separately as the original protocol recommends; report machine-level denominators and uncertainty.
4. Freeze RCAEval case-level development/holdout groups before modeling. Handle repeated cases, metric/time overlap and varying metric sets explicitly; fit preprocessing on training only. Keep root-cause/injection metadata out of inputs. Evaluate deterministic RCA under its existing role.
5. Audit whether public telemetry supplies measurable SLO/onset and actual precursor intervals. If it does, create a versioned label protocol and train XGBoost; if it does not, report that gap and select an additional suitable source. Do not relabel post-fault diagnosis as prediction.
6. Define and implement a matching live feature adapter, measure transfer/calibration and only then consider activation. The current `http-red-5m/v1` contract has five RED/trend features; SMD has 38 unnamed channels and RCAEval has heterogeneous named metrics. A successful offline fit alone cannot satisfy this compatibility gate.

## Evidence and scope

Required research artifacts: source revision/checksums, file manifest, selected entities/cases, split indices, ordered feature list, train-only preprocessing, protocol hash, code/library versions, frozen model/threshold hashes and test metrics with denominators. Binaries/raw data remain ignored artifacts; sanitized reports are committed.

The public benchmark is a concrete training/evaluation step, not a live deployment or G3/G7 completion claim. The API keeps its fail-closed model-unavailable behavior until a compatible activation exists. Local/G2 five-minute-window targets remain applicable to a selected live RED source; public rows are not counted as those windows.
