from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOAD_DIR = ROOT / "load" / "k6"


def test_load_harness_only_targets_non_billable_read_surfaces() -> None:
    scripts = {
        path.name: path.read_text(encoding="utf-8")
        for path in LOAD_DIR.glob("*.js")
    }

    assert set(scripts) == {"public-health.js", "authenticated-session-read.js"}
    joined = "\n".join(scripts.values())
    for prohibited in ("/chat", "/rag/reindex", "/ci/refresh", "/code/propose", "/approval/", "/billing"):
        assert prohibited not in joined
    assert "/health" in scripts["public-health.js"]
    assert "/api/session/all" in scripts["authenticated-session-read.js"]


def test_load_harness_requires_staging_https_and_disposable_authentication() -> None:
    read_script = (LOAD_DIR / "authenticated-session-read.js").read_text(encoding="utf-8")
    guide = (LOAD_DIR / "README.md").read_text(encoding="utf-8")

    assert 'baseUrl.startsWith("https://")' in read_script
    assert "LOAD_BEARER_TOKEN" in read_script
    assert "disposable" in guide.lower()
    assert "customer" in guide.lower()
