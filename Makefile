setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test: setup
	.venv/bin/python -m pytest -q

test-agent: setup
	.venv/bin/python -m pytest -q tests/integration/agent

.PHONY: setup test test-agent
