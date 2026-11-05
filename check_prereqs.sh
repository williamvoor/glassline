#!/usr/bin/env bash
# Check onboarding prerequisites for genUI. No installs, no side effects.
# Usage: ./check_prereqs.sh
set -uo pipefail

pass=0; fail=0
ok()   { echo "ok   $1"; pass=$((pass+1)); }
bad()  { echo "FAIL $1${2:+ ($2)}"; fail=$((fail+1)); }

have() { command -v "$1" >/dev/null 2>&1; }

# Python 3.13
if have python3 && python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2]==(3,13) else 1)'; then
  ok "python3 $(python3 -V 2>&1)"
else
  bad "python3 3.13" "$(have python3 && python3 -V 2>&1 || echo 'missing')"
fi

# uv
if have uv; then ok "uv $(uv --version 2>/dev/null)"; else bad "uv" "see https://docs.astral.sh/uv/"; fi

# Node 20+ and npm
if have node; then
  major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  if [ "${major:-0}" -ge 20 ]; then ok "node $(node --version)"; else bad "node 20+" "$(node --version)"; fi
else
  bad "node 20+" "missing"
fi
if have npm; then ok "npm $(npm --version 2>/dev/null)"; else bad "npm" "missing"; fi

# Docker + Compose v2, daemon running
if have docker; then
  if docker info >/dev/null 2>&1; then ok "docker daemon running"; else bad "docker daemon" "start Docker Desktop / dockerd"; fi
  if docker compose version >/dev/null 2>&1; then ok "docker compose v2"; else bad "docker compose v2" "update Docker"; fi
else
  bad "docker" "missing"
fi

# Shell helpers used by run scripts
for t in bash curl python3 pgrep git; do
  if have "$t"; then ok "$t"; else bad "$t" "missing"; fi
done

echo
echo "$pass passed, $fail failed."
[ "$fail" -eq 0 ] || { echo "Install what failed above, then re-run ./check_prereqs.sh."; exit 1; }
