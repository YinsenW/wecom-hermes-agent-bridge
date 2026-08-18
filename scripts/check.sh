#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ ! -x .venv/bin/python ]]; then
  uv sync --frozen --dev
fi

.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src

forbidden_pattern='BEGIN (OPENSSH|RSA|EC) PRIVATE KEY|/Users/|WECOM_(APP_SECRET|CALLBACK_TOKEN|CALLBACK_AES_KEY)=[A-Za-z0-9+/]{40,}|HERMES_API_KEY=[A-Za-z0-9_-]{40,}'
if rg -n --hidden --glob '!.git/**' --glob '!.venv/**' --glob '!uv.lock' --glob '!scripts/check.sh' "$forbidden_pattern" .; then
  echo "Potential private data found; review before publishing." >&2
  exit 1
fi

echo "Checks passed."
