"""Git identity check - ensures consistent committer identity."""

import re
import subprocess
from pathlib import Path
from typing import List

from ..utils import CheckResult


def run_checks(repo_path: str, config=None, deep: bool = False, **kwargs) -> List[CheckResult]:
    """Ensure committer identity is consistent with repo history or local config."""
    path = Path(repo_path)

    try:
        res = subprocess.run(
            ["git", "var", "GIT_AUTHOR_IDENT"],
            cwd=path, capture_output=True, text=True,
        )
        if res.returncode != 0:
            return [CheckResult("Git identity", True, [], skipped=True,
                                info="Cannot determine identity")]

        match = re.search(r"<(.*)>", res.stdout)
        if not match:
            return [CheckResult("Git identity", True, [], skipped=True,
                                info="Could not parse git identity")]
        impending_email = match.group(1).strip()

        # Check for local config
        local_check = subprocess.run(
            ["git", "config", "--local", "--get", "user.email"],
            cwd=path, capture_output=True, text=True,
        )
        has_local = local_check.returncode == 0
        local_email = local_check.stdout.strip() if has_local else None

        if has_local:
            if impending_email != local_email:
                return [CheckResult("Git identity", False, [
                    f"Impending '{impending_email}' differs from local config '{local_email}'"
                ])]

            # Check unpushed commits match
            unpushed_emails = set()
            try:
                res = subprocess.run(
                    ["git", "log", "@{u}..HEAD", "--format=%ae"],
                    cwd=path, capture_output=True, text=True, check=True,
                )
                unpushed_emails.update(e.strip() for e in res.stdout.splitlines() if e.strip())
            except subprocess.CalledProcessError:
                try:
                    res = subprocess.run(
                        ["git", "log", "--format=%ae"],
                        cwd=path, capture_output=True, text=True, check=True,
                    )
                    unpushed_emails.update(e.strip() for e in res.stdout.splitlines() if e.strip())
                except subprocess.CalledProcessError:
                    pass

            if unpushed_emails:
                mismatches = unpushed_emails - {local_email}
                if mismatches:
                    return [CheckResult("Git identity", False, [
                        f"Unpushed commits have different identity: {mismatches}"
                    ])]

            return [CheckResult("Git identity", True)]

        # No local config — history must match impending email
        res = subprocess.run(
            ["git", "log", "--format=%ae"],
            cwd=path, capture_output=True, text=True,
        )
        if res.returncode == 0:
            history_emails = set(e.strip() for e in res.stdout.splitlines() if e.strip())
            if not history_emails or history_emails == {impending_email}:
                return [CheckResult("Git identity", True)]

            # Check config for override
            skip_check = False
            if config:
                skip_check = config.raw.get("git_local_user_identity_optional", False)
            if skip_check:
                return [CheckResult("Git identity", True)]

            return [CheckResult("Git identity", False, [
                f"History has {history_emails}, impending is '{impending_email}'",
                f"Fix: git config user.email {impending_email}",
            ])]

        return [CheckResult("Git identity", True)]

    except Exception as e:
        return [CheckResult("Git identity", True, [], skipped=True,
                            info=f"Check failed: {e}")]
