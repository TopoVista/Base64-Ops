from pathlib import Path


def test_clerk_jwks_runtime_dependency_is_pinned() -> None:
    """A clean install must include the module used by auth middleware."""

    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")

    assert "PyJWT[crypto]==2.15.1" in requirements
