"""Bounded static construction for a revision-bound engineering graph.

The builder never executes repository code.  Python is parsed with ``ast``;
other supported formats use safe, deliberately narrow static extraction.  Any
relationship which cannot be resolved is represented as a limitation rather
than guessed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from app.intelligence.graph.models import (
    EngineeringGraphSnapshot,
    EpistemicStatus,
    GraphCoverage,
    GraphDiscoveryMethod,
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphRelationType,
)
from app.utils.datetime import utc_now
from app.utils.ids import new_id

MAX_GRAPH_FILES = 500
MAX_GRAPH_NODES = 4_000
MAX_GRAPH_EDGES = 8_000
_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}
_TYPESCRIPT_SUFFIXES = {".ts", ".tsx", ".js", ".jsx"}
_WORKFLOW_PREFIX = ".github/workflows/"
_IMPORT_RE = re.compile(r"(?:import|export)\s+(?:[^'\"]+?\s+from\s+)?['\"]([^'\"]+)['\"]")


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _node_ids(repository_id: str, commit_sha: str, kind: GraphNodeType, logical_suffix: str) -> tuple[str, str, str]:
    logical_id = f"{repository_id}:{kind.value}:{logical_suffix}"
    version_id = _digest(logical_id, commit_sha)
    return f"gnd_{version_id[:24]}", logical_id, version_id


def _edge_id(source_node_id: str, target_node_id: str, relation_type: GraphRelationType, commit_sha: str) -> str:
    return f"ged_{_digest(source_node_id, target_node_id, relation_type.value, commit_sha)[:24]}"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _language(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
    }.get(suffix)


class EngineeringGraphBuilder:
    """Build deterministic facts for one checked-out repository revision."""

    def build(
        self,
        *,
        repo_path: Path,
        user_id: str,
        session_id: str,
        repository_id: str,
        commit_sha: str,
        branch: str | None,
        files: Iterable[Path],
    ) -> EngineeringGraphSnapshot:
        now = utc_now()
        all_files = list(files)
        selected = all_files[:MAX_GRAPH_FILES]
        paths = {path.relative_to(repo_path).as_posix(): path for path in selected if path.is_file()}
        coverage = GraphCoverage(
            scanned_files=len(paths),
            parsed_files=0,
            skipped_files=0,
            unsupported_files=0,
            truncated=len(all_files) > MAX_GRAPH_FILES,
        )
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        node_by_key: dict[tuple[GraphNodeType, str], GraphNode] = {}

        def add_node(
            kind: GraphNodeType,
            logical_suffix: str,
            *,
            name: str,
            path: str | None = None,
            qualified_name: str | None = None,
            language: str | None = None,
            content_hash: str | None = None,
            attributes: dict[str, Any] | None = None,
            discovery_method: GraphDiscoveryMethod = GraphDiscoveryMethod.FILESYSTEM,
            verification_status: EpistemicStatus = EpistemicStatus.OBSERVED,
        ) -> GraphNode | None:
            key = (kind, logical_suffix)
            if key in node_by_key:
                return node_by_key[key]
            if len(nodes) >= MAX_GRAPH_NODES:
                coverage.truncated = True
                return None
            node_id, logical_id, version_id = _node_ids(repository_id, commit_sha, kind, logical_suffix)
            node = GraphNode(
                id=node_id,
                logical_id=logical_id,
                version_id=version_id,
                tenant_id=user_id,
                repository_id=repository_id,
                type=kind,
                name=name,
                qualified_name=qualified_name,
                path=path,
                language=language,
                commit_sha=commit_sha,
                branch=branch,
                attributes=attributes or {},
                content_hash=content_hash,
                discovery_method=discovery_method,
                verification_status=verification_status,
                first_observed_at=now,
                last_observed_at=now,
                created_at=now,
                updated_at=now,
            )
            nodes.append(node)
            node_by_key[key] = node
            return node

        def add_edge(
            source: GraphNode | None,
            target: GraphNode | None,
            relation_type: GraphRelationType,
            *,
            discovery_method: GraphDiscoveryMethod,
            epistemic_status: EpistemicStatus = EpistemicStatus.OBSERVED,
            confidence: float | None = 1.0,
            properties: dict[str, Any] | None = None,
        ) -> None:
            if not source or not target or len(edges) >= MAX_GRAPH_EDGES:
                if len(edges) >= MAX_GRAPH_EDGES:
                    coverage.truncated = True
                return
            edge_id = _edge_id(source.id, target.id, relation_type, commit_sha)
            if any(edge.id == edge_id for edge in edges):
                return
            edges.append(
                GraphEdge(
                    id=edge_id,
                    source_node_id=source.id,
                    target_node_id=target.id,
                    relation_type=relation_type,
                    repository_id=repository_id,
                    source_version=source.version_id,
                    target_version=target.version_id,
                    discovery_method=discovery_method,
                    confidence=confidence,
                    epistemic_status=epistemic_status,
                    observed_at=now,
                    validity_scope=f"commit:{commit_sha}",
                    properties=properties or {},
                )
            )

        repository = add_node(
            GraphNodeType.REPOSITORY,
            "root",
            name=Path(repository_id.rstrip("/")).stem or "repository",
            discovery_method=GraphDiscoveryMethod.FILESYSTEM,
        )
        file_nodes: dict[str, GraphNode] = {}
        texts: dict[str, str] = {}
        for relative_path, absolute_path in sorted(paths.items()):
            try:
                text = absolute_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                coverage.skipped_files += 1
                continue
            texts[relative_path] = text
            file_node = add_node(
                GraphNodeType.FILE,
                relative_path,
                name=Path(relative_path).name,
                path=relative_path,
                language=_language(relative_path),
                content_hash=_content_hash(text),
                discovery_method=GraphDiscoveryMethod.FILESYSTEM,
            )
            if file_node:
                file_nodes[relative_path] = file_node
                add_edge(
                    repository,
                    file_node,
                    GraphRelationType.CONTAINS,
                    discovery_method=GraphDiscoveryMethod.FILESYSTEM,
                )

        for relative_path, text in texts.items():
            file_node = file_nodes.get(relative_path)
            if not file_node:
                continue
            suffix = Path(relative_path).suffix.lower()
            if suffix == ".py":
                self._extract_python(
                    text=text,
                    path=relative_path,
                    source=file_node,
                    file_nodes=file_nodes,
                    add_node=add_node,
                    add_edge=add_edge,
                    coverage=coverage,
                )
                coverage.parsed_files += 1
            elif suffix in _TYPESCRIPT_SUFFIXES:
                self._extract_static_imports(
                    text=text,
                    path=relative_path,
                    source=file_node,
                    file_nodes=file_nodes,
                    add_edge=add_edge,
                )
                coverage.parsed_files += 1
            elif relative_path.startswith(_WORKFLOW_PREFIX) and suffix in {".yaml", ".yml"}:
                self._extract_workflow(
                    text=text,
                    path=relative_path,
                    source=file_node,
                    add_node=add_node,
                    add_edge=add_edge,
                    coverage=coverage,
                )
                coverage.parsed_files += 1
            elif Path(relative_path).name in {"package.json", "pyproject.toml", "requirements.txt"}:
                self._extract_manifest(
                    text=text,
                    path=relative_path,
                    source=file_node,
                    add_node=add_node,
                    add_edge=add_edge,
                    coverage=coverage,
                )
                coverage.parsed_files += 1
            else:
                coverage.unsupported_files += 1

        if coverage.truncated:
            coverage.limitations.append("Graph limits were reached; this snapshot is intentionally partial.")
        if coverage.unsupported_files:
            coverage.limitations.append(
                "Some repository files have no language-aware extractor in this graph revision."
            )
        return EngineeringGraphSnapshot(
            id=new_id("graph_"),
            user_id=user_id,
            session_id=session_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            branch=branch,
            built_at=now,
            nodes=nodes,
            edges=edges,
            coverage=coverage,
        )

    @staticmethod
    def _resolve_python_import(path: str, module: str, level: int, files: dict[str, GraphNode]) -> GraphNode | None:
        parent = Path(path).parent
        base = parent
        for _ in range(max(level - 1, 0)):
            base = base.parent
        module_path = module.replace(".", "/") if module else ""
        candidates = [
            (base / f"{module_path}.py").as_posix(),
            (base / module_path / "__init__.py").as_posix(),
            f"{module_path}.py",
            f"{module_path}/__init__.py",
        ]
        for candidate in candidates:
            if candidate in files:
                return files[candidate]
        suffix = f"/{module_path}.py" if module_path else ""
        matches = [node for name, node in files.items() if suffix and name.endswith(suffix)]
        return matches[0] if len(matches) == 1 else None

    def _extract_python(
        self,
        *,
        text: str,
        path: str,
        source: GraphNode,
        file_nodes: dict[str, GraphNode],
        add_node: Any,
        add_edge: Any,
        coverage: GraphCoverage,
    ) -> None:
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError:
            coverage.limitations.append(f"Python syntax could not be parsed: {path}")
            return
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = GraphNodeType.TEST if item.name.startswith("test_") else GraphNodeType.FUNCTION
                function = add_node(
                    kind,
                    f"{path}:{item.name}",
                    name=item.name,
                    qualified_name=f"{path}:{item.name}",
                    path=path,
                    language="python",
                    attributes={"line": item.lineno, "async": isinstance(item, ast.AsyncFunctionDef)},
                    discovery_method=GraphDiscoveryMethod.PYTHON_AST,
                )
                add_edge(source, function, GraphRelationType.CONTAINS, discovery_method=GraphDiscoveryMethod.PYTHON_AST)
                for decorator in item.decorator_list:
                    route = self._fastapi_route(decorator)
                    if route:
                        endpoint = add_node(
                            GraphNodeType.API_ENDPOINT,
                            f"{path}:{route[0]}:{route[1]}",
                            name=route[1],
                            qualified_name=f"{path}:{item.name}",
                            path=path,
                            language="python",
                            attributes={"method": route[0], "line": item.lineno},
                            discovery_method=GraphDiscoveryMethod.PYTHON_AST,
                        )
                        add_edge(
                            function,
                            endpoint,
                            GraphRelationType.EXPOSES,
                            discovery_method=GraphDiscoveryMethod.PYTHON_AST,
                        )
            elif isinstance(item, ast.ClassDef):
                class_node = add_node(
                    GraphNodeType.CLASS,
                    f"{path}:{item.name}",
                    name=item.name,
                    qualified_name=f"{path}:{item.name}",
                    path=path,
                    language="python",
                    attributes={"line": item.lineno},
                    discovery_method=GraphDiscoveryMethod.PYTHON_AST,
                )
                add_edge(
                    source,
                    class_node,
                    GraphRelationType.CONTAINS,
                    discovery_method=GraphDiscoveryMethod.PYTHON_AST,
                )
        for item in ast.walk(tree):
            module: str | None = None
            level = 0
            if isinstance(item, ast.Import):
                for alias in item.names:
                    target = self._resolve_python_import(path, alias.name, 0, file_nodes)
                    add_edge(
                        source,
                        target,
                        GraphRelationType.IMPORTS,
                        discovery_method=GraphDiscoveryMethod.PYTHON_AST,
                    )
            elif isinstance(item, ast.ImportFrom):
                module = item.module
                level = item.level
                if module is not None:
                    target = self._resolve_python_import(path, module, level, file_nodes)
                    add_edge(
                        source,
                        target,
                        GraphRelationType.IMPORTS,
                        discovery_method=GraphDiscoveryMethod.PYTHON_AST,
                    )

    @staticmethod
    def _fastapi_route(decorator: ast.expr) -> tuple[str, str] | None:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            return None
        method = decorator.func.attr.lower()
        if method not in {"get", "post", "put", "patch", "delete", "options", "head"}:
            return None
        if (
            not decorator.args
            or not isinstance(decorator.args[0], ast.Constant)
            or not isinstance(decorator.args[0].value, str)
        ):
            return None
        return method.upper(), decorator.args[0].value

    @staticmethod
    def _resolve_relative_import(path: str, specifier: str, files: dict[str, GraphNode]) -> GraphNode | None:
        if specifier.startswith("@/"):
            candidates = [f"src/{specifier[2:]}"]
        elif specifier.startswith("."):
            candidates = [(Path(path).parent / specifier).as_posix()]
        else:
            return None
        for candidate in candidates:
            for suffix in ("", ".ts", ".tsx", ".js", ".jsx"):
                if f"{candidate}{suffix}" in files:
                    return files[f"{candidate}{suffix}"]
            for suffix in (".ts", ".tsx", ".js", ".jsx"):
                index_path = f"{candidate}/index{suffix}"
                if index_path in files:
                    return files[index_path]
        return None

    def _extract_static_imports(
        self, *, text: str, path: str, source: GraphNode, file_nodes: dict[str, GraphNode], add_edge: Any
    ) -> None:
        for match in _IMPORT_RE.finditer(text):
            target = self._resolve_relative_import(path, match.group(1), file_nodes)
            add_edge(
                source,
                target,
                GraphRelationType.IMPORTS,
                discovery_method=GraphDiscoveryMethod.STATIC_IMPORT,
                epistemic_status=EpistemicStatus.OBSERVED,
                confidence=0.8,
            )

    def _extract_workflow(
        self, *, text: str, path: str, source: GraphNode, add_node: Any, add_edge: Any, coverage: GraphCoverage
    ) -> None:
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError:
            coverage.limitations.append(f"Workflow YAML could not be safely parsed: {path}")
            return
        if not isinstance(raw, dict):
            return
        workflow = add_node(
            GraphNodeType.WORKFLOW,
            path,
            name=str(raw.get("name") or Path(path).stem),
            path=path,
            language="yaml",
            discovery_method=GraphDiscoveryMethod.YAML_SAFE_PARSE,
        )
        add_edge(source, workflow, GraphRelationType.CONTAINS, discovery_method=GraphDiscoveryMethod.YAML_SAFE_PARSE)
        jobs = raw.get("jobs")
        if not isinstance(jobs, dict):
            return
        for job_id, job in jobs.items():
            if not isinstance(job_id, str) or not isinstance(job, dict):
                continue
            job_node = add_node(
                GraphNodeType.CI_JOB,
                f"{path}:{job_id}",
                name=str(job.get("name") or job_id),
                qualified_name=job_id,
                path=path,
                language="yaml",
                attributes={
                    "jobId": job_id,
                    "stepCount": len(job.get("steps", [])) if isinstance(job.get("steps"), list) else 0,
                },
                discovery_method=GraphDiscoveryMethod.YAML_SAFE_PARSE,
            )
            add_edge(
                workflow,
                job_node,
                GraphRelationType.CONTAINS,
                discovery_method=GraphDiscoveryMethod.YAML_SAFE_PARSE,
            )
            needs = job.get("needs")
            dependencies = [needs] if isinstance(needs, str) else needs if isinstance(needs, list) else []
            for dependency in dependencies:
                if not isinstance(dependency, str):
                    continue
                target = add_node(
                    GraphNodeType.CI_JOB,
                    f"{path}:{dependency}",
                    name=dependency,
                    qualified_name=dependency,
                    path=path,
                    language="yaml",
                    discovery_method=GraphDiscoveryMethod.YAML_SAFE_PARSE,
                    verification_status=EpistemicStatus.UNRESOLVED,
                )
                add_edge(
                    job_node,
                    target,
                    GraphRelationType.DEPENDS_ON,
                    discovery_method=GraphDiscoveryMethod.YAML_SAFE_PARSE,
                )

    def _extract_manifest(
        self, *, text: str, path: str, source: GraphNode, add_node: Any, add_edge: Any, coverage: GraphCoverage
    ) -> None:
        packages: list[str] = []
        try:
            if path.endswith("package.json"):
                raw = json.loads(text)
                if isinstance(raw, dict):
                    for key in ("dependencies", "devDependencies"):
                        values = raw.get(key)
                        if isinstance(values, dict):
                            packages.extend(name for name in values if isinstance(name, str))
            elif path.endswith("requirements.txt"):
                packages.extend(
                    line.split("=", 1)[0].split("<", 1)[0].split(">", 1)[0].strip()
                    for line in text.splitlines()
                    if line.strip() and not line.lstrip().startswith(("#", "-"))
                )
            elif path.endswith("pyproject.toml"):
                for match in re.finditer(r"^\s*([A-Za-z0-9_.-]+)\s*=", text, re.MULTILINE):
                    packages.append(match.group(1))
        except (ValueError, json.JSONDecodeError):
            coverage.limitations.append(f"Manifest could not be parsed: {path}")
            return
        for package_name in sorted(set(package for package in packages if package))[:500]:
            package = add_node(
                GraphNodeType.PACKAGE,
                package_name.lower(),
                name=package_name,
                path=path,
                discovery_method=GraphDiscoveryMethod.MANIFEST_PARSE,
            )
            add_edge(
                source, package, GraphRelationType.DEPENDS_ON, discovery_method=GraphDiscoveryMethod.MANIFEST_PARSE
            )
