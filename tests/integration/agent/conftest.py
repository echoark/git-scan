"""Tests of the review's own model call: git-scan review with the real
claude preset. Skipped when the claude CLI is not installed; it runs on the
CLI's own login, no API key. Excluded from the default pytest run
(pyproject addopts); run with: make test-agent."""

import shutil

import pytest

requires_claude = pytest.mark.skipif(shutil.which("claude") is None,
                                     reason="the claude CLI is not installed")

MODEL = "claude-opus-5-5"     # pinned so a run is comparable with the last one
