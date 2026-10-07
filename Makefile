setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test: setup
	.venv/bin/python -m pytest -q

.PHONY: setup test
