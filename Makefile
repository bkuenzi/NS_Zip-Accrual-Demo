.PHONY: install lint test demo mvp dataset export-web clean

install:
	uv sync --extra dev

lint:
	uv run ruff check .

test:
	uv run pytest

demo:
	uv run accrual-agent demo --profile demo

mvp:
	uv run accrual-agent demo --profile mvp

dataset:
	python scripts/generate_seatgeek_dataset.py
	python scripts/validate_seatgeek_dataset.py

export-web:
	uv run accrual-agent export-web --profile demo
	uv run accrual-agent export-web --profile mvp

clean:
	rm -rf data output .pytest_cache .ruff_cache
