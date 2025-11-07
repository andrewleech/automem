"""Git context capture for memory storage"""

import subprocess
from datetime import datetime, timezone
from typing import Optional


def capture_git_context() -> Optional[dict]:
    """Capture current git context (branch, commit, modified files, status)

    Returns:
        Dict with git context or None if not in a git repo or git unavailable:
        {
            "describe": "v1.2.3-14-gabc1234-dirty",
            "branch": "feat/project-isolation",
            "modified_files": ["app.py", "cli.py"],
            "status": "dirty",  # or "clean"
            "captured_at": "2025-11-06T10:30:00Z"
        }
    """
    try:
        # Check if in git repo
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            check=True,
            timeout=2
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None

    try:
        context = {}

        # Get git describe (version/commit info)
        describe_result = subprocess.run(
            ["git", "describe", "--always", "--dirty", "--tags"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if describe_result.returncode == 0:
            context["describe"] = describe_result.stdout.strip()

        # Get current branch
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if branch_result.returncode == 0:
            branch = branch_result.stdout.strip()
            if branch:
                context["branch"] = branch
            else:
                # Detached HEAD - use commit hash
                commit_result = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if commit_result.returncode == 0:
                    context["branch"] = f"detached@{commit_result.stdout.strip()}"

        # Check if working tree is dirty or clean
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=2
        )
        modified_files = []
        if status_result.returncode == 0:
            # Parse modified tracked files (not untracked)
            for line in status_result.stdout.strip().split("\n"):
                if not line:
                    continue
                # Format: "XY filename" where X is index status, Y is working tree status
                # Skip untracked files (??), only get actual modifications
                if line[:2] != "??":
                    filename = line[3:]
                    modified_files.append(filename)

        if modified_files:
            context["status"] = "dirty"
            context["modified_files"] = modified_files
        else:
            context["status"] = "clean"

        # Add capture timestamp
        context["captured_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        return context if context else None

    except (subprocess.TimeoutExpired, Exception):
        # Silently skip on any git errors
        return None
