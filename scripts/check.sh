#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ ! -x .venv/bin/python ]]; then
  uv sync --frozen --dev
fi

.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src

forbidden_pattern='BEGIN (OPENSSH|RSA|EC) PRIVATE KEY|/Users/|45\.62\.119\.61|cherry-ai\.com|千彗科技|xinming'
if rg -n --hidden --glob '!.git/**' --glob '!.venv/**' --glob '!uv.lock' --glob '!scripts/check.sh' "$forbidden_pattern" .; then
  echo "Potential private data found; review before publishing." >&2
  exit 1
fi

echo "Checks passed."
