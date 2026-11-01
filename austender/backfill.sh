#!/usr/bin/env bash
# Full AusTender backfill, then rebuild core and mart. Safe to re-run: completed months are skipped.
#
#   ./backfill.sh                    # 2013-01 to the last complete UTC month
#   ./backfill.sh 2020-01 2020-12    # explicit range
#
# Takes about 3 hours. Run it inside tmux (or similar) so closing the terminal doesn't kill it.
# Output also goes to logs/backfill-<timestamp>.log.
set -euo pipefail
cd "$(dirname "$0")"

# Last complete UTC month. Set the day to 1 first so the month arithmetic can't overflow (e.g. 31 Oct).
last_month() {
  date -u -v1d -v-1m +%Y-%m 2>/dev/null || date -u -d "$(date -u +%Y-%m-01) -1 month" +%Y-%m
}

from="${1:-2013-01}"
to="${2:-$(last_month)}"

mkdir -p logs
log="logs/backfill-$(date -u +%Y%m%dT%H%M%SZ).log"
export PYTHONUNBUFFERED=1   # so progress reaches the log as it happens

# Keep a Mac awake while this script runs; sleep would drop the API and DB connections.
if command -v caffeinate >/dev/null; then
  caffeinate -i -w $$ &
fi

{
  echo "backfill $from to $to, started $(date -u +%FT%TZ)"
  uv run austender backfill --from "$from" --to "$to" --resume
  echo "rebuild started $(date -u +%FT%TZ)"
  time uv run austender rebuild
  uv run austender stats
  echo "finished $(date -u +%FT%TZ)"
} 2>&1 | tee "$log"
