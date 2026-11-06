#!/usr/bin/env bash
# Regenerate an Explorer Dataset and all its Boards from one of the repo's databases, through the
# bootstrap CLI: report → draft → verify → boards → export (explorer/bootstrap/README.md).
#
# Usage: ./regen_dataset.sh [<db-dir>] [options]        (no <db-dir>: pick from a list)
# e.g.   ./regen_dataset.sh austender
#        ./regen_dataset.sh austender --keep notes.md,glossary.md      # new Boards, old notes
#        ./regen_dataset.sh vicprop --load migrate --load rebuild --model meta/muse-spark-1.3
#        ./regen_dataset.sh austender --work-dir /tmp/regen-x --from boards   # resume
#
# Run with --help for every option. Steps that call the model (draft, boards) cost money and
# print their cost. Work files (draft.json, boards.json, regen.log) hold database-derived text:
# they go outside the repo by default.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$ROOT/explorer/bootstrap/server"
DATASETS_DIR="$ROOT/datasets"
STEPS=(report draft verify boards export)

usage() {
  cat <<'EOF'
Usage: ./regen_dataset.sh [<db-dir>] [options]

Database
  <db-dir>              repo directory with docker-compose.yml and .env (austender, vicprop, ...);
                        omitted: choose from a list
  --load ARGS           run `uv run <db-dir> ARGS` in the db dir before drafting (repeatable,
                        in order), e.g. --load migrate --load rebuild
  --no-db-start         don't run `docker compose up -d --wait` (database already reachable)

Dataset
  --name NAME           dataset directory under datasets (default: <db-dir>)
  --schemas A,B         draft from these schemas instead of the existing dataset's allowed
                        schemas (required when the dataset directory doesn't exist yet)
  --target toml|sql     comments as a comments.toml overlay or comments.sql (default: toml)
  --keep F1,F2          after export, restore these files from the backup (dataset.toml,
                        notes.md, glossary.md, comments.toml, comments.sql), e.g. to keep
                        reviewed notes and only take the new Boards
  --no-boards           skip the Boards loop (export the Dataset files only; old Boards stay)

Model
  --model M             meta/muse-wonder-1.0 (default) or meta/muse-spark-1.3
  --board-model M       model for the Boards loop (default: --model)
  --effort E            reasoning effort (default: the bootstrap's, low)
  --timeout-ms N        statement_timeout per query (default 5000)

Flow
  --work-dir DIR        where draft.json, boards.json and regen.log go
                        (default: $TMPDIR/genui-regen/<name>-<UTC stamp>)
  --from STEP           start at report|draft|verify|boards|export (resume in --work-dir)
  --to STEP             stop after this step
  --edit                open $EDITOR on draft.json and boards.json at the review pauses
  --yes                 no pauses and no confirmation: export as soon as checks pass
  --dry-run             print what would run, run nothing
  -h, --help            this help
EOF
}

die() { echo "error: $*" >&2; exit 2; }
say() { printf '\n==> %s\n' "$*"; }

# Read KEY from a dotenv file without sourcing it (so nothing else leaks into the environment).
env_get() {
  local key="$1" file="$2" line val
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 0
  val="${line#*=}"
  val="${val%$'\r'}"
  if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then
    val="${BASH_REMATCH[1]}"
  fi
  printf '%s' "$val"
}

step_index() {
  local i
  for i in "${!STEPS[@]}"; do
    [[ "${STEPS[$i]}" == "$1" ]] && { echo "$i"; return 0; }
  done
  die "unknown step '$1' (one of: ${STEPS[*]})"
}

# --- arguments ---
DB_DIR="" NAME="" SCHEMAS="" TARGET="toml" KEEP="" NO_BOARDS=0 MODEL="" BOARD_MODEL="" EFFORT=""
TIMEOUT_MS="5000" WORK_DIR="" FROM="report" TO="export" EDIT=0 YES=0 DRY_RUN=0 DB_START=1
LOADS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --load) [[ $# -ge 2 ]] || die "--load needs a value"; LOADS+=("$2"); shift 2 ;;
    --no-db-start) DB_START=0; shift ;;
    --name) NAME="${2:-}"; shift 2 ;;
    --schemas) SCHEMAS="${2:-}"; shift 2 ;;
    --target) TARGET="${2:-}"; shift 2 ;;
    --keep) KEEP="${2:-}"; shift 2 ;;
    --no-boards) NO_BOARDS=1; shift ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --board-model) BOARD_MODEL="${2:-}"; shift 2 ;;
    --effort) EFFORT="${2:-}"; shift 2 ;;
    --timeout-ms) TIMEOUT_MS="${2:-}"; shift 2 ;;
    --work-dir) WORK_DIR="${2:-}"; shift 2 ;;
    --from) FROM="${2:-}"; shift 2 ;;
    --to) TO="${2:-}"; shift 2 ;;
    --edit) EDIT=1; shift ;;
    --yes) YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --*) die "unknown option $1 (see --help)" ;;
    *) [[ -z "$DB_DIR" ]] || die "only one <db-dir>"; DB_DIR="$1"; shift ;;
  esac
done

[[ "$TARGET" == "toml" || "$TARGET" == "sql" ]] || die "--target must be toml or sql"
[[ "$TIMEOUT_MS" =~ ^[1-9][0-9]*$ ]] || die "--timeout-ms must be a positive integer"
FROM_I="$(step_index "$FROM")"
TO_I="$(step_index "$TO")"
(( FROM_I <= TO_I )) || die "--from $FROM comes after --to $TO"
if [[ -n "$KEEP" ]]; then
  IFS=',' read -r -a KEEP_FILES <<<"$KEEP"
  for f in "${KEEP_FILES[@]}"; do
    case "$f" in
      dataset.toml|notes.md|glossary.md|comments.toml|comments.sql) ;;
      *) die "--keep: '$f' isn't a file export replaces" ;;
    esac
  done
else
  KEEP_FILES=()
fi

# --- database selection ---
db_dirs() {
  local d
  for d in "$ROOT"/*/; do
    d="${d%/}"
    [[ -f "$d/docker-compose.yml" && -f "$d/.env" && -f "$d/pyproject.toml" ]] && basename "$d"
  done
}
if [[ -z "$DB_DIR" ]]; then
  CHOICES=()
  while IFS= read -r d; do CHOICES+=("$d"); done < <(db_dirs)  # no mapfile in macOS bash 3.2
  (( ${#CHOICES[@]} > 0 )) || die "no database directories found (need docker-compose.yml, .env, pyproject.toml)"
  [[ -t 0 ]] || die "no <db-dir> given and no terminal to ask on (one of: ${CHOICES[*]})"
  echo "Databases:"
  PS3="Pick one: "
  select DB_DIR in "${CHOICES[@]}"; do
    [[ -n "$DB_DIR" ]] && break
  done
fi
DB_PATH="$ROOT/$DB_DIR"
[[ -d "$DB_PATH" && -f "$DB_PATH/docker-compose.yml" ]] || die "'$DB_DIR' has no docker-compose.yml"
[[ -f "$DB_PATH/.env" ]] || die "$DB_DIR/.env is missing (copy $DB_DIR/.env.example and set passwords)"

NAME="${NAME:-$DB_DIR}"
[[ "$NAME" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "--name must be lower-case letters, digits, _ or -"
DEST="$DATASETS_DIR/$NAME"
if [[ -n "$SCHEMAS" ]]; then
  SELECT_ARGS=(--schemas "$SCHEMAS")
elif [[ -f "$DEST/dataset.toml" ]]; then
  SELECT_ARGS=(--dataset-dir "$DEST")
else
  die "datasets/$NAME doesn't exist yet: pass --schemas"
fi
FORCE_ARGS=()
[[ -d "$DEST" ]] && FORCE_ARGS=(--force)
(( ${#KEEP_FILES[@]} == 0 )) || [[ -d "$DEST" ]] || die "--keep needs an existing datasets/$NAME"

# --- work dir ---
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -z "$WORK_DIR" ]]; then
  (( FROM_I == 0 )) || die "--from $FROM needs --work-dir (the run to resume)"
  WORK_DIR="${TMPDIR:-/tmp}"
  WORK_DIR="${WORK_DIR%/}/genui-regen/$NAME-$STAMP"
fi
case "$(cd "$(dirname "$WORK_DIR")" 2>/dev/null && pwd)/" in
  "$ROOT"/*) echo "warning: --work-dir is inside the repo; its files hold database-derived text, keep them out of git" >&2 ;;
esac
DRAFT="$WORK_DIR/draft.json"
BOARDS="$WORK_DIR/boards.json"
REPORT="$WORK_DIR/report.json"
LOG="$WORK_DIR/regen.log"

run() {
  printf '  $'; printf ' %q' "$@"; printf '\n'
  (( DRY_RUN )) && return 0
  "$@"
}

# The bootstrap CLI, from its own directory (its REPO_ROOT and DATASETS_ROOT are fixed there).
boot() { (cd "$SERVER_DIR" && run uv run --locked python -m explorer_bootstrap "$@"); }

pause() {
  (( YES || DRY_RUN )) && return 0
  local file="$1" msg="$2" reply
  echo
  echo "$msg"
  if (( EDIT )); then
    "${EDITOR:-vi}" "$file" </dev/tty >/dev/tty
  else
    echo "  file: $file"
  fi
  read -r -p "Press Enter to continue, or type q to stop: " reply </dev/tty
  [[ "$reply" != q* ]] || { echo "Stopped. Resume with: $0 $DB_DIR --work-dir $WORK_DIR --from ${3:-draft}"; exit 0; }
}

want() { local i; i="$(step_index "$1")"; (( i >= FROM_I && i <= TO_I )); }

# --- plan + confirmation ---
echo "Regenerate dataset '$NAME' from database '$DB_DIR'"
echo "  selection : ${SELECT_ARGS[*]}"
echo "  model     : ${MODEL:-default (BOOTSTRAP_MODEL or meta/muse-wonder-1.0)}, Boards: ${BOARD_MODEL:-${MODEL:-same}}"
echo "  comments  : $TARGET   Boards: $([[ $NO_BOARDS == 1 ]] && echo skipped || echo regenerated)"
echo "  steps     : ${STEPS[*]:$FROM_I:$((TO_I - FROM_I + 1))}"
echo "  work dir  : $WORK_DIR"
if want export && [[ -d "$DEST" ]]; then
  echo "  export    : --force into datasets/$NAME (replaced files go to a .${NAME}.bak-* backup)"
  [[ $NO_BOARDS == 1 ]] || echo "              boards/ is replaced as a whole"
  (( ${#KEEP_FILES[@]} == 0 )) || echo "              then restored from the backup: ${KEEP_FILES[*]}"
  if [[ -f "$DEST/scenarios.toml" ]]; then
    echo "  note      : scenarios.toml stays, but Board scenarios name Controls and row text;"
    echo "              new Boards may break them. Rerun the scenarios after review."
  fi
  if git -C "$ROOT" status --porcelain -- "datasets/$NAME" | grep -q .; then
    echo "  warning   : datasets/$NAME has uncommitted changes (they'll only be in the backup)"
  fi
fi
if (( ! YES && ! DRY_RUN )); then
  read -r -p "Proceed? [y/N] " ok </dev/tty
  [[ "$ok" == y* || "$ok" == Y* ]] || { echo "Nothing done."; exit 0; }
fi

if (( ! DRY_RUN )); then
  mkdir -p "$WORK_DIR"
  chmod 700 "$WORK_DIR"
  exec > >(tee -a "$LOG") 2>&1
  echo "--- $STAMP regen_dataset.sh $DB_DIR (name $NAME, from $FROM to $TO)"
fi

# --- database ---
if (( DB_START )); then
  say "Starting the $DB_DIR database"
  (cd "$DB_PATH" && run docker compose up -d --wait)
fi
for args in "${LOADS[@]+"${LOADS[@]}"}"; do
  say "Loading: uv run $DB_DIR $args"
  # shellcheck disable=SC2086  # ARGS is split on purpose, like a command line
  (cd "$DB_PATH" && run uv run "$DB_DIR" $args)
done

# Read-only DSN from the db dir's .env. Exported so it wins over the root .env (existing
# environment values win in the bootstrap's settings). Never printed.
PG_HOST="$(env_get POSTGRES_HOST "$DB_PATH/.env")"
PG_PORT="$(env_get POSTGRES_PORT "$DB_PATH/.env")"
PG_DB="$(env_get POSTGRES_DB "$DB_PATH/.env")"
RO_PW="$(env_get EXPLORER_RO_PASSWORD "$DB_PATH/.env")"
[[ -n "$PG_PORT" && -n "$PG_DB" && -n "$RO_PW" ]] \
  || die "$DB_DIR/.env needs POSTGRES_PORT, POSTGRES_DB and EXPLORER_RO_PASSWORD"
ENC_PW="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$RO_PW")"
export BOOTSTRAP_DATABASE_URL="postgresql://explorer_ro:${ENC_PW}@${PG_HOST:-localhost}:${PG_PORT}/${PG_DB}"
unset RO_PW ENC_PW

(cd "$SERVER_DIR" && run uv sync --locked --quiet)

MODEL_ARGS=()
[[ -n "$MODEL" ]] && MODEL_ARGS+=(--model "$MODEL")
[[ -n "$EFFORT" ]] && MODEL_ARGS+=(--effort "$EFFORT")
BOARD_ARGS=(--model "${BOARD_MODEL:-${MODEL:-meta/muse-wonder-1.0}}")
[[ -z "$BOARD_MODEL" && -z "$MODEL" ]] && BOARD_ARGS=()
[[ -n "$EFFORT" ]] && BOARD_ARGS+=(--effort "$EFFORT")

# --- steps ---
if want report; then
  say "Report (no model)"
  boot report "${SELECT_ARGS[@]}" --json "$REPORT" --timeout-ms "$TIMEOUT_MS"
fi

if want draft; then
  say "Draft the Dataset (calls the model)"
  boot draft "${SELECT_ARGS[@]}" --out "$DRAFT" --timeout-ms "$TIMEOUT_MS" "${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}"
  pause "$DRAFT" "Review the draft: edit files and comments, set \"accepted\": true on comment rewrites you want, fix a claim's sql/expect or set \"dismissed\": true." verify
fi

if want verify; then
  (( DRY_RUN )) || [[ -f "$DRAFT" ]] || die "no $DRAFT (run the draft step first)"
  while true; do
    say "Verify claims (no model)"
    status=0
    boot verify --draft "$DRAFT" --timeout-ms "$TIMEOUT_MS" || status=$?
    (( status == 0 )) && break
    (( status == 1 )) || die "verify failed (exit $status)"
    if (( YES )); then
      echo "Claims block export. Fix or dismiss them, then: $0 $DB_DIR --work-dir $WORK_DIR --from verify"
      exit 1
    fi
    pause "$DRAFT" "Claims block export: fix them (sql/expect) or set \"dismissed\": true, then verify again." verify
  done
fi

if want boards && (( ! NO_BOARDS )); then
  (( DRY_RUN )) || [[ -f "$DRAFT" ]] || die "no $DRAFT (run the draft step first)"
  say "Draft the Boards (calls the model)"
  boot boards --draft "$DRAFT" --out "$BOARDS" --timeout-ms "$TIMEOUT_MS" "${BOARD_ARGS[@]+"${BOARD_ARGS[@]}"}"
  pause "$BOARDS" "Review the Boards (preview above): edit the TOML in boards.json if needed. Export re-checks them." export
fi

if want export; then
  (( DRY_RUN )) || [[ -f "$DRAFT" ]] || die "no $DRAFT"
  EXPORT_ARGS=(--draft "$DRAFT" --name "$NAME" --target "$TARGET" --root "$DATASETS_DIR" --timeout-ms "$TIMEOUT_MS")
  if (( ! NO_BOARDS )); then
    (( DRY_RUN )) || [[ -f "$BOARDS" ]] || die "no $BOARDS (run the boards step, or pass --no-boards)"
    EXPORT_ARGS+=(--boards "$BOARDS")
  fi
  say "Export to datasets/$NAME"
  out="$WORK_DIR/export.out"
  if (( DRY_RUN )); then
    boot export "${EXPORT_ARGS[@]}" "${FORCE_ARGS[@]+"${FORCE_ARGS[@]}"}"
  else
    status=0
    boot export "${EXPORT_ARGS[@]}" "${FORCE_ARGS[@]+"${FORCE_ARGS[@]}"}" | tee "$out" || status=$?
    (( status == 0 )) || { echo "Export refused or failed (exit $status). Fix, then: $0 $DB_DIR --work-dir $WORK_DIR --from export"; exit 1; }
    BACKUP="$(sed -n 's/^Previous files kept in //p' "$out")"
    if (( ${#KEEP_FILES[@]} > 0 )); then
      [[ -n "$BACKUP" && -d "$BACKUP" ]] || die "--keep: export made no backup to restore from"
      for f in "${KEEP_FILES[@]}"; do
        if [[ -f "$BACKUP/$f" ]]; then
          cp -p "$BACKUP/$f" "$DEST/$f"
          echo "  kept $f (restored from the backup)"
        else
          echo "  --keep: $f wasn't in the backup; the new one stays"
        fi
      done
      if printf '%s\n' "${KEEP_FILES[@]}" | grep -qx dataset.toml; then
        echo "  note: the old dataset.toml may not point at the new comments file; check its comments setting"
      fi
    fi
    say "Changes in datasets/$NAME"
    git -C "$ROOT" status --short -- "datasets/$NAME"
    echo
    echo "Next: review the diff, then open it with: (cd explorer && ./run.sh)"
    [[ -f "$DEST/scenarios.toml" ]] && echo "      and rerun its scenarios before committing."
    [[ "$TARGET" == "sql" ]] && echo "      Apply comments.sql as the objects' owner first."
    [[ -n "$BACKUP" ]] && echo "Backup: $BACKUP (delete it once you're happy; the dataset picker ignores it)"
  fi
fi

(( DRY_RUN )) || echo "Work files and log: $WORK_DIR"
