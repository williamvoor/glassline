#!/usr/bin/env bash
# Start (or stop) the Explorer demo from scratch: no Workspaces, both databases (austender,
# vicprop) offered as logins in Settings, so each Workspace is bootstrapped live
# (explorer/specs/workspaces/SPEC.md). Real databases; Muse Spark answers, Muse Wonder drafts.
#
# Usage: ./run_demo.sh [start] [--keep] [--data-dir DIR]
#        ./run_demo.sh stop
#
#   start           (default) start both databases and the explorer with an empty data directory.
#                   An earlier one is moved aside (kept as <dir>.bak-<time>), never deleted.
#                   Open http://127.0.0.1:5173, then Settings > Add a Workspace (a draft plus
#                   Boards costs about $0.30 in model calls). Ctrl-C stops it.
#   --keep          keep the data directory: the Workspaces and Conversations made earlier
#   --data-dir DIR  the Workspace data directory (default ~/.explorer-demo; outside the repo)
#   stop            stop whatever serves the agent (10002), client (5173) and bootstrap (8765)
#
# Nothing is written to .env: each database's read-only login is built from its own .env
# (<db>/.env: POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, EXPLORER_RO_PASSWORD) and exported only
# to the processes started here. Keys and passwords are never printed. The model key and other
# settings still come from the repo root's .env.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTS=(10002 5173 8765) # agent, client, bootstrap server: explorer/run.sh's defaults

ACTION=start
KEEP=0
DATA_DIR="$HOME/.explorer-demo"

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    start|stop) ACTION="$1" ;;
    --keep) KEEP=1 ;;
    --data-dir)
      [ -n "${2:-}" ] || { echo "error: --data-dir needs a value" >&2; exit 2; }
      DATA_DIR="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

listeners() { lsof -ti "tcp:$(IFS=,; echo "${PORTS[*]}")" -sTCP:LISTEN 2>/dev/null || true; }

if [ "$ACTION" = stop ]; then
  pids="$(listeners)"
  if [ -z "$pids" ]; then echo "nothing is serving on ports ${PORTS[*]}"; exit 0; fi
  echo "--> stopping $(echo "$pids" | wc -l | tr -d ' ') process(es) on ports ${PORTS[*]}"
  # shellcheck disable=SC2086
  kill $pids
  for _ in $(seq 1 20); do [ -z "$(listeners)" ] && break; sleep 0.25; done
  [ -z "$(listeners)" ] || { echo "error: still serving; check with: lsof -i tcp:10002,5173,8765" >&2; exit 1; }
  echo "stopped (the databases keep running)"
  exit 0
fi

# --- checks ----------------------------------------------------------------------------------
case "$DATA_DIR" in
  /*) ;;
  *) echo "error: --data-dir must be an absolute path" >&2; exit 2 ;;
esac
case "$DATA_DIR/" in
  "$ROOT"/*) echo "error: the data directory must be outside the repo (it holds saved questions)" >&2; exit 2 ;;
esac
for tool in docker uv npm python3 lsof; do
  command -v "$tool" >/dev/null || { echo "error: $tool is not on the PATH" >&2; exit 1; }
done
[ -f "$ROOT/.env" ] || { echo "error: no $ROOT/.env (copy explorer/.env.example and fill it in)" >&2; exit 1; }
if [ -n "$(listeners)" ]; then
  echo "error: something already serves one of ports ${PORTS[*]} (an earlier explorer?)." >&2
  echo "       Stop it with: ./run_demo.sh stop" >&2
  exit 1
fi

# --- databases -------------------------------------------------------------------------------
# Prints the read-only DSN for <db>. Reads only the four keys it needs from <db>/.env, without
# sourcing it: a value bash can't parse would stop the script and echo the line.
ro_dsn() {
  python3 - "$ROOT/$1/.env" "$1" <<'PY'
import sys, urllib.parse
path, db = sys.argv[1], sys.argv[2]
env = {}
for line in open(path, encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.removeprefix("export ").split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    env[key.strip()] = value
missing = [k for k in ("POSTGRES_PORT", "POSTGRES_DB", "EXPLORER_RO_PASSWORD") if not env.get(k)]
if missing:
    sys.exit(f"error: {', '.join(missing)} not set in {db}/.env")
pw = urllib.parse.quote(env["EXPLORER_RO_PASSWORD"], safe="")
host = env.get("POSTGRES_HOST") or "localhost"
print(f"postgresql://explorer_ro:{pw}@{host}:{env['POSTGRES_PORT']}/{env['POSTGRES_DB']}")
PY
}

for db in austender vicprop; do
  [ -f "$ROOT/$db/.env" ] || { echo "error: no $db/.env (see $db/README.md)" >&2; exit 1; }
  echo "--> starting the $db database"
  (cd "$ROOT/$db" && docker compose up -d --wait >/dev/null)
done
AUSTENDER_DATABASE_URL="$(ro_dsn austender)"
VICPROP_DATABASE_URL="$(ro_dsn vicprop)"
export AUSTENDER_DATABASE_URL VICPROP_DATABASE_URL

# --- data directory: empty unless --keep --------------------------------------------------
if [ "$KEEP" = 0 ] && [ -e "$DATA_DIR" ]; then
  backup="$DATA_DIR.bak-$(date +%Y%m%dT%H%M%S)"
  mv "$DATA_DIR" "$backup"
  echo "--> moved the previous data directory to $backup"
fi
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"
if [ -f "$DATA_DIR/workspaces.toml" ]; then
  echo "--> keeping the Workspaces in $DATA_DIR"
else
  echo "--> no Workspaces yet: open http://127.0.0.1:5173, then Settings > Add a Workspace"
fi

# --- run -------------------------------------------------------------------------------------
export EXPLORER_DATA_DIR="$DATA_DIR"
echo "--> starting the explorer (agent, bootstrap server, client); Ctrl-C stops it"
exec "$ROOT/explorer/run.sh" --real
