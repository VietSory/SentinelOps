.PHONY: install test validate run

install:
	python3 -m pip install -e '.[dev]'

test:
	pytest -q

validate:
	python scripts/validate_contracts.py

run:
	uvicorn sentinelops.main:app --app-dir src --reload
