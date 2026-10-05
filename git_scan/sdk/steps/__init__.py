"""Auto-discovery for scanner steps.

Each module in this package should have a run_checks(repo_path, ...) function
that returns a List[CheckResult].
"""

import pkgutil
import importlib
from typing import List

from ..utils import CheckResult


def discover_steps() -> List[tuple]:
    """Discover all step modules and their run_checks functions."""
    steps = []
    for finder, name, ispkg in pkgutil.iter_modules(__path__):
        if name.startswith("_"):
            continue
        mod = importlib.import_module(f".{name}", __name__)
        if hasattr(mod, "run_checks"):
            steps.append((name, mod.run_checks))
    return steps


def run_all_steps(repo_path: str, config, deep: bool = False,
                  only_step: str = None,
                  include_untracked: bool = False) -> List[CheckResult]:
    """Run all discovered steps and collect results."""
    results = []
    for name, run_checks in discover_steps():
        if only_step is not None and name != only_step:
            continue
        try:
            step_results = run_checks(
                repo_path, config=config, deep=deep,
                include_untracked=include_untracked,
            )
            results.extend(step_results)
        except Exception as e:
            results.append(CheckResult(
                name=f"{name} (error)",
                passed=False,
                findings=[str(e)],
                error=str(e),
            ))
    return results


def get_step_names() -> List[str]:
    """Get list of all discovered step module names."""
    return [name for name, _ in discover_steps()]
