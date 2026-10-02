import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from fastapi import HTTPException, status

from app.core.config import get_settings
from app.services.github_service import get_github_access_token

SAFE_READ_COMMANDS = {
    "git status",
    "git diff",
    "git log",
    "docker compose config",
    "docker-compose config",
}


def repo_name_from_url(repo_url: str) -> str:
    name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", name) or "repo"


def workspace_path(user_id: str, slug_id: str) -> Path:
    root = get_settings().workspace_root
    safe_user = re.sub(r"[^a-zA-Z0-9._-]+", "-", user_id)
    safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug_id)
    return (root / safe_user / safe_slug).resolve()


def branch_name_from_prompt(prompt: str | None) -> str:
    suffix = "changes"
    if prompt:
        suffix = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:32] or "changes"
    return f"agent/{suffix}"


class WorkspaceService:
    async def ensure_workspace(
        self,
        user_id: str,
        slug_id: str,
        repo_url: str,
        default_branch: str | None,
    ) -> tuple[Path, str]:
        path = workspace_path(user_id, slug_id)
        repo_name = repo_name_from_url(repo_url)
        repo_path = path / repo_name
        if repo_path.exists() and (repo_path / ".git").exists():
            return repo_path, repo_name
        if repo_path.exists():
            # A previous clone may have been interrupted (expired OAuth,
            # network loss, or a cancelled first index). This is a
            # Base64-managed workspace path, never a user-supplied path. A
            # partial directory cannot be repaired by git clone, so remove it
            # before rebuilding the workspace from the remote repository.
            if repo_path.parent.resolve() != path.resolve():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsafe workspace path")
            if repo_path.is_dir():
                shutil.rmtree(repo_path)
            else:
                repo_path.unlink()
        path.mkdir(parents=True, exist_ok=True)
        token = await get_github_access_token(user_id)
        authed_url = self._with_token(repo_url, token)
        args = ["git", "clone", "--depth", "1"]
        if default_branch:
            args.extend(["--branch", default_branch])
        args.extend([authed_url, repo_name])
        result = self._run(args, cwd=path, hide_token=token)
        if result["exitCode"] != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to clone repository: {result['output']}",
            )
        return repo_path, repo_name

    async def ensure_branch(self, repo_path: Path, branch_name: str) -> dict[str, Any]:
        result = self._run(["git", "checkout", "-B", branch_name], cwd=repo_path)
        return result

    def read_file(self, repo_path: Path, relative_path: str) -> str:
        target = self._safe_path(repo_path, relative_path)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        return target.read_text(encoding="utf-8", errors="replace")

    def read_file_at_ref(self, repo_path: Path, relative_path: str, ref: str) -> str | None:
        """Read a tracked file at a Git ref without checking out or mutating the workspace."""
        self._safe_path(repo_path, relative_path)
        normalized_path = relative_path.replace("\\", "/")
        result = self._run(["git", "show", f"{ref}:{normalized_path}"], cwd=repo_path)
        return result["output"] if result["success"] else None

    def write_file(self, repo_path: Path, relative_path: str, content: str) -> dict[str, Any]:
        target = self._safe_path(repo_path, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": relative_path, "bytes": len(content.encode("utf-8"))}

    def grep(self, repo_path: Path, query: str) -> dict[str, Any]:
        matches: list[str] = []
        for file_path in self.iter_indexable_files(repo_path):
            text = file_path.read_text(encoding="utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if query.lower() in line.lower():
                    matches.append(f"{file_path.relative_to(repo_path)}:{line_number}: {line.strip()}")
                    if len(matches) >= 50:
                        return {"matches": matches, "count": len(matches)}
        return {"matches": matches, "count": len(matches)}

    def git_status(self, repo_path: Path) -> dict[str, Any]:
        return self._run(["git", "status", "--short"], cwd=repo_path)

    def git_diff(self, repo_path: Path) -> dict[str, Any]:
        return self._run(["git", "diff", "--stat"], cwd=repo_path)

    def head_commit(self, repo_path: Path) -> str | None:
        result = self._run(["git", "rev-parse", "HEAD"], cwd=repo_path)
        return result["output"].strip() if result["success"] else None

    def parent_commit(self, repo_path: Path, ref: str) -> str | None:
        """Return a commit parent using Git only; this never changes checkout state."""
        result = self._run(["git", "rev-parse", f"{ref}^"], cwd=repo_path)
        return result["output"].strip() if result["success"] else None

    def current_branch(self, repo_path: Path) -> str | None:
        result = self._run(["git", "branch", "--show-current"], cwd=repo_path)
        return result["output"].strip() if result["success"] else None

    def recent_commits(self, repo_path: Path, limit: int = 12) -> list[dict[str, str]]:
        """Return a bounded, presentation-safe local commit history.

        Git is queried only inside the session's cloned workspace. Commit
        messages remain repository evidence and are never treated as commands.
        """
        safe_limit = max(1, min(limit, 30))
        result = self._run(
            [
                "git",
                "log",
                f"--max-count={safe_limit}",
                "--date=short",
                "--pretty=format:%H%x1f%h%x1f%an%x1f%ad%x1f%s",
            ],
            cwd=repo_path,
        )
        if not result["success"]:
            return []
        commits = []
        for line in result["output"].splitlines():
            parts = line.split("\x1f", 4)
            if len(parts) != 5:
                continue
            commit_sha, short_sha, author, committed_on, subject = parts
            commits.append(
                {
                    "sha": commit_sha,
                    "shortSha": short_sha,
                    "author": author,
                    "date": committed_on,
                    "subject": subject,
                }
            )
        return commits

    def hydrate_recent_history(self, repo_path: Path, branch: str | None, depth: int = 24) -> None:
        """Extend a shallow clone only enough for the Code history panel.

        Fetching commit objects updates Base64's disposable local clone; it
        never checks out a branch, changes files, or writes to GitHub. A
        permission/network failure simply leaves the locally available history
        intact.
        """
        shallow_marker = repo_path / ".git" / "shallow"
        if not shallow_marker.exists():
            return
        safe_depth = max(1, min(depth, 50))
        args = ["git", "fetch", f"--deepen={safe_depth}", "origin"]
        if branch:
            args.append(branch)
        self._run(args, cwd=repo_path)

    def repository_map(self, repo_path: Path) -> dict[str, Any]:
        files = [str(path.relative_to(repo_path)).replace("\\", "/") for path in self.iter_indexable_files(repo_path)]
        return {
            "services": [
                path for path in files if path.endswith(("main.py", "server.py", "package.json", "Dockerfile"))
            ],
            "containers": [path for path in files if "docker" in path.lower()],
            "ci": [path for path in files if path.startswith(".github/workflows/")],
            "manifests": [
                path
                for path in files
                if path.rsplit("/", 1)[-1]
                in {"package.json", "pyproject.toml", "requirements.txt", "docker-compose.yml", "compose.yml"}
            ],
            "runbooks": [
                path
                for path in files
                if path.lower().startswith(("docs/", ".base64ops/playbooks/")) or path.lower().endswith("runbook.md")
            ],
            "fileCount": len(files),
        }

    def dependency_graph(self, repo_path: Path) -> dict[str, Any]:
        """Build a bounded, read-only graph of local source imports.

        This is deliberately a static hint, not a build-system or runtime
        dependency resolver. It never executes package scripts or imports.
        """
        source_paths = {
            str(path.relative_to(repo_path)).replace("\\", "/"): path
            for path in self.iter_indexable_files(repo_path)
            if path.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx", ".go"}
        }
        edges: set[tuple[str, str]] = set()
        extensions = (".ts", ".tsx", ".js", ".jsx", ".py")

        go_module: str | None = None
        go_mod = repo_path / "go.mod"
        if go_mod.exists():
            module_match = re.search(
                r"^\s*module\s+([^\s]+)",
                go_mod.read_text(encoding="utf-8", errors="replace"),
                re.MULTILINE,
            )
            if module_match:
                go_module = module_match.group(1)

        def resolve_relative(source: str, specifier: str) -> str | None:
            if specifier.startswith("@/"):
                candidates = [f"src/{specifier[2:]}"]
            elif specifier.startswith("."):
                candidates = [str((Path(source).parent / specifier).as_posix())]
            else:
                return None
            for candidate in candidates:
                for suffix in ("", *extensions):
                    target = f"{candidate}{suffix}"
                    if target in source_paths:
                        return target
                for suffix in extensions:
                    target = f"{candidate}/index{suffix}"
                    if target in source_paths:
                        return target
            return None

        def resolve_python(specifier: str) -> str | None:
            normalized = specifier.replace(".", "/")
            for target in (f"{normalized}.py", f"{normalized}/__init__.py"):
                if target in source_paths:
                    return target
            # Repositories do not always expose a single top-level package.
            matches = [path for path in source_paths if path.endswith(f"/{normalized}.py")]
            return matches[0] if len(matches) == 1 else None

        def resolve_go(specifier: str) -> str | None:
            """Resolve local Go module imports without resolving external modules."""
            if not go_module or not specifier.startswith(f"{go_module}/"):
                return None
            package_path = specifier.removeprefix(f"{go_module}/")
            matches = [
                path for path in source_paths
                if Path(path).parent.as_posix() == package_path
            ]
            return matches[0] if len(matches) == 1 else None

        for source, file_path in source_paths.items():
            text = file_path.read_text(encoding="utf-8", errors="replace")
            if file_path.suffix.lower() == ".py":
                for match in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", text, re.MULTILINE):
                    target = resolve_python(match.group(1))
                    if target and target != source:
                        edges.add((source, target))
            elif file_path.suffix.lower() in {".ts", ".tsx", ".js", ".jsx"}:
                for match in re.finditer(r"(?:import|export)\s+(?:[^'\"]+?\s+from\s+)?['\"]([^'\"]+)['\"]", text):
                    target = resolve_relative(source, match.group(1))
                    if target and target != source:
                        edges.add((source, target))
            elif file_path.suffix.lower() == ".go":
                for match in re.finditer(r'^\s*"([^"\n]+)"', text, re.MULTILINE):
                    target = resolve_go(match.group(1))
                    if target and target != source:
                        edges.add((source, target))

        # Showing leaf files matters: otherwise a valid repository with no local
        # imports is indistinguishable from one the graph could not inspect.
        selected_paths = sorted(source_paths)[:120]
        selected = set(selected_paths)
        selected_edges = sorted(edge for edge in edges if edge[0] in selected and edge[1] in selected)[:240]

        def mermaid_label(path: str) -> str:
            return path.replace('"', "'").replace("\\", "/")

        node_ids = {path: f"F{index}" for index, path in enumerate(selected_paths)}
        lines = ["flowchart LR"]
        lines.extend(f'  {node_ids[path]}["{mermaid_label(path)}"]' for path in selected_paths)
        lines.extend(f"  {node_ids[source]} --> {node_ids[target]}" for source, target in selected_edges)
        return {
            "headSha": self.head_commit(repo_path),
            "nodes": selected_paths,
            "edges": [{"from": source, "to": target} for source, target in selected_edges],
            "mermaid": "\n".join(lines),
            "truncated": len(edges) > len(selected_edges),
        }

    def docker_build_check(self, repo_path: Path) -> dict[str, Any]:
        if not (repo_path / "Dockerfile").exists():
            return {"success": False, "output": "Dockerfile not found", "exitCode": 1}
        return self._run(["docker", "build", "--no-cache", "--pull", "-t", "agent-check:latest", "."], cwd=repo_path)

    def compose_config_check(self, repo_path: Path) -> dict[str, Any]:
        if (repo_path / "docker-compose.yml").exists() or (repo_path / "compose.yml").exists():
            return self._run(["docker", "compose", "config"], cwd=repo_path)
        return {"success": False, "output": "No compose file found", "exitCode": 1}

    def commit_all(self, repo_path: Path, message: str) -> dict[str, Any]:
        self._run(["git", "add", "-A"], cwd=repo_path)
        return self._run(["git", "commit", "-m", message], cwd=repo_path)

    def push_branch(self, repo_path: Path, branch_name: str) -> dict[str, Any]:
        return self._run(["git", "push", "-u", "origin", branch_name], cwd=repo_path)

    def iter_indexable_files(self, repo_path: Path) -> list[Path]:
        ignored_dirs = {".git", "node_modules", "dist", "build", ".next", ".venv", "__pycache__"}
        extensions = {
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".py",
            ".go",
            ".md",
            ".mdx",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".env.example",
            ".dockerfile",
        }
        files: list[Path] = []
        for root, dirs, names in os.walk(repo_path):
            dirs[:] = [directory for directory in dirs if directory not in ignored_dirs]
            for name in names:
                path = Path(root) / name
                suffix = path.suffix.lower()
                if name == "Dockerfile" or suffix in extensions:
                    if path.stat().st_size <= 250_000:
                        files.append(path)
        return files[:500]

    def _safe_path(self, repo_path: Path, relative_path: str) -> Path:
        target = (repo_path / relative_path).resolve()
        if not str(target).startswith(str(repo_path.resolve())):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsafe path")
        return target

    def _with_token(self, repo_url: str, token: str) -> str:
        parsed = urlparse(repo_url)
        if parsed.scheme not in {"http", "https"}:
            return repo_url
        netloc = f"x-access-token:{quote(token)}@{parsed.netloc}"
        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))

    def _run(
        self,
        args: list[str],
        cwd: Path,
        timeout: int = 120,
        hide_token: str | None = None,
    ) -> dict[str, Any]:
        if shutil.which(args[0]) is None:
            return {"success": False, "output": f"{args[0]} is not installed", "exitCode": 127}
        completed = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        output = (completed.stdout + completed.stderr).strip()
        if hide_token:
            output = output.replace(hide_token, "***")
        return {"success": completed.returncode == 0, "output": output, "exitCode": completed.returncode}
