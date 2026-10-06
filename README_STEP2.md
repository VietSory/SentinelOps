# SentinelOps — Step 2 overlay

Add these files on top of the completed Foundation v0.1 repository.

This overlay deliberately replaces `pyproject.toml` because Checkout now uses `httpx` at runtime and Payment/Inventory use `asyncpg`.

## Start the testbed

```bash
docker compose up --build -d
docker compose ps
```

Check readiness:

```bash
curl http://127.0.0.1:8001/ready
curl http://127.0.0.1:8002/ready
curl http://127.0.0.1:8003/ready
```

Run the full smoke scenario:

```bash
python scripts/step2_smoke_test.py
```

Inspect cross-service structured logs:

```bash
docker compose logs checkout-service payment-service inventory-service
```

Stop without deleting data:

```bash
docker compose down
```

Reset the entire local testbed including the database volume:

```bash
./scripts/step2_reset.sh
```

Detailed intent and DoD: `docs/STEP2_TESTBED.md`.
