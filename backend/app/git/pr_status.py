"""Pull request status provider — read-only GitHub PR state lookup.

This module provides a thin, typed adapter over the existing ``github_api``
helper.  It does NOT introduce a second GitHub client; all HTTP is delegated
to ``app.services.github_service.github_api``.

Safety rules
------------
- Read-only: no write/mutation GitHub calls.
- All calls are tenant-scoped (user_id required).
- Tokens are never stored or logged here.
- A GitHub failure returns None rather than raising, so callers can apply
  a safe fallback (leave current state unchanged).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PR state model
# ---------------------------------------------------------------------------


class PullRequestStatus(BaseModel):
    """Typed representation of a GitHub pull request's current state."""

    repository_id: str
    number: int

    state: Literal["open", "closed"]
    draft: bool

    merged: bool
    merge_commit_sha: str | None = None
    merged_at: datetime | None = None

    base_branch: str | None = None
    head_sha: str | None = None

    title: str | None = None
    html_url: str | None = None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class PullRequestStatusProvider:
    """Read-only GitHub PR status provider.

    Usage
    -----
    provider = PullRequestStatusProvider()
    status = await provider.get(
        user_id="user_1",
        repository_id="https://github.com/owner/repo",
        pr_number=17,
    )
    if status and status.merged:
        ...
    """

    async def get(
        self,
        *,
        user_id: str,
        repository_id: str,
        pr_number: int,
    ) -> PullRequestStatus | None:
        """Fetch the current state of a pull request.

        Returns None when:
        - GitHub is not authenticated (no access token for user).
        - The PR does not exist (404).
        - Any network/API error occurs.

        Callers must treat None as "state unknown" and leave existing
        memory records unchanged.
        """
        owner_repo = self._owner_repo(repository_id)
        if not owner_repo:
            logger.debug("pr_status: cannot parse repository_id %r", repository_id)
            return None

        try:
            from app.services.github_service import github_api

            data: dict[str, Any] = await github_api(
                user_id,
                "GET",
                f"/repos/{owner_repo}/pulls/{pr_number}",
            )
            if not data:
                return None

            return self._parse(data, repository_id)

        except Exception as exc:
            # Log at debug — callers handle None gracefully.
            logger.debug(
                "pr_status: lookup failed for %s#%s: %s",
                owner_repo,
                pr_number,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(data: dict[str, Any], repository_id: str) -> PullRequestStatus:
        merged_at_raw = data.get("merged_at")
        merged_at: datetime | None = None
        if merged_at_raw:
            try:
                merged_at = datetime.fromisoformat(merged_at_raw.rstrip("Z") + "+00:00")
            except ValueError:
                pass

        return PullRequestStatus(
            repository_id=repository_id,
            number=data["number"],
            state=data.get("state", "open"),
            draft=bool(data.get("draft", False)),
            merged=bool(data.get("merged", False)),
            merge_commit_sha=data.get("merge_commit_sha"),
            merged_at=merged_at,
            base_branch=(data.get("base") or {}).get("ref"),
            head_sha=(data.get("head") or {}).get("sha"),
            title=data.get("title"),
            html_url=data.get("html_url"),
        )

    @staticmethod
    def _owner_repo(repository_id: str) -> str | None:
        import re

        m = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", repository_id)
        if m:
            return m.group(1)
        if re.match(r"^[^/]+/[^/]+$", repository_id):
            return repository_id
        return None
