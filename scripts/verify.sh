#!/usr/bin/env sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin="${PYTHON_BIN:-$root/backend/venv/bin/python}"
if [ ! -x "$python_bin" ]; then python_bin="python"; fi
( cd "$root/backend" && "$python_bin" -m pytest -q && "$python_bin" -m ruff check . && "$python_bin" -m evals.runner --mode deterministic && "$python_bin" -m evals.runner --mode deterministic --output ../artifacts/eval-summary.json --format json && "$python_bin" -m evals.runner --mode deterministic --category ci && "$python_bin" -c 'from app.agent.graph import AgentGraph; print(type(AgentGraph().graph).__name__)' )
( cd "$root/client" && npx tsc --noEmit && npm run build )
