# SentinelOps — Step 2 testbed entry point

The former overlay installation instructions are superseded: Step 2 is integrated into this repository. Do not overwrite `pyproject.toml` using an old overlay.

Use [README.md](README.md) for installation and [docs/STEP2_TESTBED.md](docs/STEP2_TESTBED.md) for workload behavior. The current product baseline is [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md); the full implementation design and delivery gates are linked there.

```bash
docker compose up --build -d --wait --wait-timeout 120
python scripts/step2_smoke_test.py
python scripts/step3_smoke_test.py
```

These commands preserve the database volume. `scripts/step2_reset.sh` is a destructive sandbox reset; use it only when discarding that sandbox's database is intentional.
