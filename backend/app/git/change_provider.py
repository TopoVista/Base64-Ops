"""Git change provider — determines which paths changed between two commits.

Architecture
------------
Preferred source: local repository workspace (git diff --name-status).
Fallback source:  GitHub Commits/Compare API (read-only, authenticated).

Neither source is required.  When both are unavailable the result carries
``complete=False`` and ``source="unavailable"`` so callers can apply the
conservative "needs_revalidation" policy rather than assuming unchanged.

Safety rules
------------
- No secrets are stored or logged.
- Only path names are returned; no file content.
- Subprocess calls use a fixed argument list (no shell=True, no string concat).
- Repository isolation: the caller supplies the workspace path; this module
  never resolves workspace paths independently.
- GitHub calls use the existing authenticated ``github_api`` helper; no second
  HTTP client is constructed here.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class RenamedPath(BaseModel):
    """A file that was renamed between two commits."""

    old_path: str
    new_path: str
    # Similarity percentage reported by git (0–100).  None when unavailable.
    similarity: int | None = None


class ChangedPathsResult(BaseModel):
    """Paths that changed between base_sha and head_sha.

    ``complete`` is False when the lookup failed or was truncated.
    ``source`` identifies how the result was obtained.
    ``warning`` carries a human-readable explanation when ``complete`` is False.
    """

    base_sha: str
    head_sha: str

    # All modified/added/deleted/renamed paths (normalised, repo-relative).
    changed_paths: list[str] = Field(default_factory=list)

    renamed_paths: list[RenamedPath] = Field(default_factory=list)
    deleted_paths: list[str] = Field(default_factory=list)

    source: Literal["local_git", "github_compare", "unavailable"]

    # True when the result is believed complete; False when truncated or failed.
    complete: bool

    warning: str | None = None


# ---------------------------------------------------------------------------
# Local git comparison
# ---------------------------------------------------------------------------


def _parse_name_status(output: str) -> ChangedPathsResult | None:
    """Parse ``git diff --name-status`` output into a ChangedPathsResult skeleton.

    Returns None on parse failure.
    """
    changed: list[str] = []
    renamed: list[RenamedPath] = []
    deleted: list[str] = []

    for line in output.splitlines():
        line = line.rstrip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status_code = parts[0].strip()
        if status_code.startswith("R"):
            # R100\told_path\tnew_path  OR  R\told\tnew
            similarity = None
            m = re.match(r"R(\d+)", status_code)
            if m:
                similarity = int(m.group(1))
            if len(parts) >= 3:
                old_path = _normalize(parts[1])
                new_path = _normalize(parts[2])
                renamed.append(RenamedPath(old_path=old_path, new_path=new_path, similarity=similarity))
                changed.append(new_path)
                changed.append(old_path)
        elif status_code == "D" and len(parts) >= 2:
            path = _normalize(parts[1])
            deleted.append(path)
            changed.append(path)
        elif len(parts) >= 2:
            # A, M, C, T, U, X — treat all as changed
            changed.append(_normalize(parts[1]))

    return ChangedPathsResult(
        base_sha="",
        head_sha="",
        changed_paths=sorted(set(changed)),
        renamed_paths=renamed,
        deleted_paths=sorted(set(deleted)),
        source="local_git",
        complete=True,
    )


def _normalize(path: str) -> str:
    """Normalise to forward slashes and strip leading ./"""
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


class GitChangeProvider:
    """Computes changed paths between two commits.

    Usage
    -----
    result = await provider.changed_paths(
        base_sha="abc123",
        head_sha="def456",
        repo_path=Path("/workspaces/user/slug/repo"),
        user_id="user_1",           # for GitHub fallback auth
        repository_id="https://github.com/owner/repo",
    )
    """

    # Maximum lines of name-status output to parse (prevents OOM on giant diffs).
    _MAX_LINES = 2000
    # Maximum GitHub API pages to fetch when comparing commits.
    _MAX_GITHUB_PAGES = 5
    _FILES_PER_PAGE = 100

    async def changed_paths(
        self,
        *,
        base_sha: str,
        head_sha: str,
        repo_path: Path | None = None,
        user_id: str | None = None,
        repository_id: str | None = None,
    ) -> ChangedPathsResult:
        """Return paths changed between ``base_sha`` and ``head_sha``.

        Tries local git first, falls back to GitHub Compare API, and if both
        are unavailable returns an incomplete result (``complete=False``).
        """
        if not base_sha or not head_sha:
            return ChangedPathsResult(
                base_sha=base_sha or "",
                head_sha=head_sha or "",
                source="unavailable",
                complete=False,
                warning="base_sha or head_sha is empty; cannot determine changed paths.",
            )

        if base_sha == head_sha:
            return ChangedPathsResult(
                base_sha=base_sha,
                head_sha=head_sha,
                changed_paths=[],
                source="local_git",
                complete=True,
            )

        # --- Attempt 1: local git ---
        if repo_path is not None:
            local = self._local_diff(base_sha, head_sha, repo_path)
            if local is not None:
                return local

        # --- Attempt 2: GitHub Compare API ---
        if user_id and repository_id:
            github = await self._github_compare(base_sha, head_sha, user_id, repository_id)
            if github is not None:
                return github

        # --- Fallback: unavailable ---
        return ChangedPathsResult(
            base_sha=base_sha,
            head_sha=head_sha,
            source="unavailable",
            complete=False,
            warning=(
                "Could not determine changed paths: "
                "local git history unavailable and GitHub compare not configured."
            ),
        )

    # ------------------------------------------------------------------
    # Local git
    # ------------------------------------------------------------------

    def _local_diff(self, base_sha: str, head_sha: str, repo_path: Path) -> ChangedPathsResult | None:
        """Run ``git diff --name-status`` and parse output.

        Returns None on any failure so the caller can fall back.
        """
        if not shutil.which("git"):
            return None
        if not (repo_path / ".git").exists():
            return None

        try:
            result = subprocess.run(
                ["git", "diff", "--name-status", f"{base_sha}..{head_sha}"],
                cwd=repo_path,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                logger.debug(
                    "git_change_provider: diff failed for %s..%s: %s",
                    base_sha[:8],
                    head_sha[:8],
                    stderr[:200],
                )
                # Unreachable commit: returncode 128 with "unknown revision"
                if "unknown revision" in stderr or "fatal" in stderr.lower():
                    return ChangedPathsResult(
                        base_sha=base_sha,
                        head_sha=head_sha,
                        source="local_git",
                        complete=False,
                        warning=(
                            f"Historical source commit is unavailable for comparison: {stderr[:200]}"
                        ),
                    )
                return None

            lines = result.stdout.splitlines()
            if len(lines) > self._MAX_LINES:
                # Parse what we have but mark incomplete
                partial = _parse_name_status("\n".join(lines[: self._MAX_LINES]))
                if partial:
                    partial.base_sha = base_sha
                    partial.head_sha = head_sha
                    partial.complete = False
                    partial.warning = (
                        f"Diff output truncated at {self._MAX_LINES} lines; "
                        "result may be incomplete."
                    )
                    return partial

            parsed = _parse_name_status(result.stdout)
            if parsed is None:
                return None
            parsed.base_sha = base_sha
            parsed.head_sha = head_sha
            return parsed

        except subprocess.TimeoutExpired:
            logger.warning("git_change_provider: diff timed out for %s..%s", base_sha[:8], head_sha[:8])
            return None
        except Exception as exc:
            logger.debug("git_change_provider: unexpected error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # GitHub Compare API fallback
    # ------------------------------------------------------------------

    async def _github_compare(
        self,
        base_sha: str,
        head_sha: str,
        user_id: str,
        repository_id: str,
    ) -> ChangedPathsResult | None:
        """Use GitHub's compare endpoint to list changed files.

        Returns None on any failure so the caller can fall back.
        """
        owner_repo = self._owner_repo_from_url(repository_id)
        if not owner_repo:
            return None

        try:
            from app.services.github_service import github_api

            all_files: list[str] = []
            renamed: list[RenamedPath] = []
            deleted: list[str] = []
            truncated = False

            # Use the commits endpoint (paginated) rather than the single compare
            # endpoint which has a 300-file limit.
            page = 1
            while page <= self._MAX_GITHUB_PAGES:
                data = await github_api(
                    user_id,
                    "GET",
                    f"/repos/{owner_repo}/compare/{base_sha}...{head_sha}",
                    params={"per_page": self._FILES_PER_PAGE, "page": page},
                )
                if data is None:
                    break

                files = data.get("files") or []
                if not files:
                    break

                for f in files:
                    filename = _normalize(f.get("filename", ""))
                    status = f.get("status", "")
                    if status == "renamed":
                        old = _normalize(f.get("previous_filename", filename))
                        renamed.append(RenamedPath(old_path=old, new_path=filename))
                        all_files.append(filename)
                        all_files.append(old)
                    elif status == "removed":
                        deleted.append(filename)
                        all_files.append(filename)
                    else:
                        all_files.append(filename)

                # GitHub compare doesn't paginate files; break after first page.
                if data.get("status") == "ahead":
                    # Pagination not standard here — one call is the result.
                    if len(files) >= self._FILES_PER_PAGE:
                        truncated = True
                    break
                break

            return ChangedPathsResult(
                base_sha=base_sha,
                head_sha=head_sha,
                changed_paths=sorted(set(all_files)),
                renamed_paths=renamed,
                deleted_paths=sorted(set(deleted)),
                source="github_compare",
                complete=not truncated,
                warning=(
                    "GitHub compare result may be truncated (>300 files)."
                    if truncated
                    else None
                ),
            )

        except Exception as exc:
            logger.debug(
                "git_change_provider: GitHub compare failed for %s..%s: %s",
                base_sha[:8],
                head_sha[:8],
                exc,
            )
            return None

    @staticmethod
    def _owner_repo_from_url(repository_id: str) -> str | None:
        """Extract 'owner/repo' from a GitHub URL or return None."""
        # https://github.com/owner/repo  or  owner/repo
        import re as _re

        m = _re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", repository_id)
        if m:
            return m.group(1)
        if _re.match(r"^[^/]+/[^/]+$", repository_id):
            return repository_id
        return None
