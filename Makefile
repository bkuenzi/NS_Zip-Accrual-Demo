.PHONY: install lint test demo dataset clean

install:
	uv sync --extra dev

lint:
	uv run ruff check .

test:
	uv run pytest

demo:
	uv run accrual-agent demo

dataset:
	python scripts/generate_seatgeek_dataset.py
	python scripts/validate_seatgeek_dataset.py

clean:
	rm -rf data output .pytest_cache .ruff_cache
