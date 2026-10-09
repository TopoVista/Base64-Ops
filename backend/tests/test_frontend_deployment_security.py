import json
from pathlib import Path


def test_vercel_configuration_has_browser_security_headers_and_hashed_asset_caching() -> None:
    root = Path(__file__).resolve().parents[2]
    config = json.loads((root / "client" / "vercel.json").read_text(encoding="utf-8"))
    headers_by_source = {item["source"]: item["headers"] for item in config["headers"]}

    asset_headers = {item["key"]: item["value"] for item in headers_by_source["/assets/(.*)"]}
    security_headers = {item["key"]: item["value"] for item in headers_by_source["/(.*)"]}

    assert config["framework"] == "vite"
    assert config["installCommand"] == "npm ci"
    assert config["buildCommand"] == "npm run build"
    assert config["outputDirectory"] == "dist"
    assert asset_headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert "frame-ancestors 'none'" in security_headers["Content-Security-Policy"]
    assert security_headers["X-Frame-Options"] == "DENY"
    assert security_headers["X-Content-Type-Options"] == "nosniff"
    assert "payment=()" in security_headers["Permissions-Policy"]
