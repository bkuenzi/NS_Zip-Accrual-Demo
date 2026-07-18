.PHONY: install lint test demo clean

install:
	uv sync --extra dev

lint:
	uv run ruff check .

test:
	uv run pytest

demo:
	uv run accrual-agent demo

clean:
	rm -rf data output .pytest_cache .ruff_cache
