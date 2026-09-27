#!/usr/bin/env sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
command -v python >/dev/null || { echo "Missing prerequisite: python" >&2; exit 1; }
command -v node >/dev/null || { echo "Missing prerequisite: node" >&2; exit 1; }
command -v npm >/dev/null || { echo "Missing prerequisite: npm" >&2; exit 1; }
echo "Base64 Ops deterministic demo setup"
[ -f "$root/backend/.env" ] || echo "Create backend/.env from backend/.env.example before authenticated services."
[ -f "$root/client/.env" ] || echo "Create client/.env from client/.env.example before the UI."
echo "Run ./scripts/verify.sh, then follow docs/DEMO_SCRIPT.md."
