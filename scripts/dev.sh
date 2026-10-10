#!/usr/bin/env bash
#
# Build and run the platform locally.
#
#   ./scripts/dev.sh doctor    what is missing, and nothing else (no side effects)
#   ./scripts/dev.sh setup     create the venv, install both dependency sets, fill .env gaps
#   ./scripts/dev.sh build     run the check suite and produce the production frontend bundle
#   ./scripts/dev.sh run       start the API and the dev server together
#
# This exists because "build and run" is four commands in two languages with one
# environment file between them, and the failure modes are quiet. A missing
# `DATABASE_URL` does not stop the app booting — `/health` reports
# `database_configured: false` and every query fails later. An empty `WEBHOOK_SECRET`
# does not stop the app booting either; deliveries are rejected with
# `webhook_secret_not_configured`, which is correct fail-closed behaviour and looks
# like a broken integration. `doctor` names both before they cost anyone an afternoon.
#
# Kafka and Redis are genuinely optional. `KAFKA_ENABLED` and `REDIS_ENABLED` default
# to false, `CACHE_LOCAL_FALLBACK` and `EVENTS_INLINE_FALLBACK` default to true, so
# with no infrastructure at all the app caches in-process and dispatches events
# inline. Postgres is the only hard dependency.
#
# No value from any .env file is ever printed. Keys are reported as set or empty,
# because the whole point of a preflight is to be safe to paste into a bug report.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"

# Overridable so this never hardcodes a host or port. The defaults match the dev
# proxy's own default target in vite.config.js; changing one means changing both.
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"

# The venv must match the version the project targets; 3.10 cannot parse some of
# the type syntax in app/.
PYTHON="${PYTHON:-python3.12}"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; }
bad()  { printf '  \033[31mMISS\033[0m  %s\n' "$1"; }

problems=0

# --- key presence, without ever revealing a value ----------------------------
# Returns 0 when the key exists in the file with a non-empty value.
env_key_set() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 1
  awk -F= -v want="$key" '
    /^[[:space:]]*[A-Za-z_]/ {
      k = $1; gsub(/[[:space:]]/, "", k)
      if (k == want) {
        v = substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", v)
        if (length(v) > 0) { found = 1 }
      }
    }
    END { exit(found ? 0 : 1) }
  ' "$file"
}

doctor() {
  bold "Toolchain"

  if command -v "$PYTHON" >/dev/null 2>&1; then
    ok "$PYTHON ($("$PYTHON" -V 2>&1))"
  else
    bad "$PYTHON not on PATH - the backend targets 3.12; set PYTHON= to override"
    problems=$((problems + 1))
  fi

  if command -v node >/dev/null 2>&1; then
    ok "node ($(node -v))"
  else
    bad "node not on PATH"
    problems=$((problems + 1))
  fi

  if command -v docker >/dev/null 2>&1; then
    ok "docker present (only needed for Kafka and Redis, both optional)"
  else
    warn "docker absent - fine unless you want KAFKA_ENABLED or REDIS_ENABLED true"
  fi

  bold "Backend dependencies"

  # A venv built on another OS leaves a dangling interpreter symlink and compiled
  # extensions for the wrong platform, which is a confusing failure if not named.
  if [ ! -x "$VENV/bin/python" ]; then
    if [ -d "$VENV" ]; then
      bad ".venv exists but its interpreter is not executable - built on another machine; delete it and run setup"
    else
      bad ".venv missing - run: ./scripts/dev.sh setup"
    fi
    problems=$((problems + 1))
  elif "$VENV/bin/python" -c "import fastapi, sqlalchemy, alembic" >/dev/null 2>&1; then
    ok ".venv imports fastapi, sqlalchemy and alembic"
  else
    bad ".venv exists but the stack does not import - run: ./scripts/dev.sh setup"
    problems=$((problems + 1))
  fi

  bold "Frontend dependencies"

  if [ ! -d "$FRONTEND/node_modules" ]; then
    bad "node_modules missing - run: ./scripts/dev.sh setup"
    problems=$((problems + 1))
  elif node -e "require.resolve('vite/package.json', {paths:['$FRONTEND']})" >/dev/null 2>&1; then
    # Vite resolving is not enough: rollup and esbuild ship per-platform binaries,
    # and an installed tree from a different OS resolves fine and then fails the
    # build with "Cannot find module @rollup/rollup-<platform>".
    if node -e "
      const {platform, arch} = process;
      const map = {darwin:'darwin', linux:'linux', win32:'win32'};
      const want = '@rollup/rollup-' + map[platform] + '-' + arch + (platform === 'linux' ? '-gnu' : '');
      require.resolve(want + '/package.json', {paths:['$FRONTEND']});
    " >/dev/null 2>&1; then
      ok "node_modules has the rollup binary for this platform"
    else
      bad "node_modules was installed for a different platform - delete it and run setup"
      problems=$((problems + 1))
    fi
  else
    bad "node_modules present but vite does not resolve - run: ./scripts/dev.sh setup"
    problems=$((problems + 1))
  fi

  bold "Configuration"

  if [ ! -f "$BACKEND/.env" ]; then
    bad "backend/.env missing - setup will copy .env.example, then fill DATABASE_URL and the Clerk keys"
    problems=$((problems + 1))
  else
    ok "backend/.env present"

    # Postgres is the only hard dependency; everything else has a working default.
    for key in DATABASE_URL CLERK_SECRET_KEY CLERK_ISSUER; do
      if env_key_set "$BACKEND/.env" "$key"; then
        ok "$key is set"
      else
        bad "$key is empty - required"
        problems=$((problems + 1))
      fi
    done

    if env_key_set "$BACKEND/.env" WEBHOOK_SECRET; then
      ok "WEBHOOK_SECRET is set"
    else
      warn "WEBHOOK_SECRET empty - the app runs, but every delivery is rejected with webhook_secret_not_configured"
    fi
  fi

  echo
  if [ "$problems" -gt 0 ]; then
    bold "$problems blocking problem(s). Fix those, or run: ./scripts/dev.sh setup"
    return 1
  fi
  bold "Ready. ./scripts/dev.sh build, then ./scripts/dev.sh run"
}

setup() {
  bold "Backend"

  # Refuse to reuse a venv from another OS rather than half-fixing it.
  if [ -d "$VENV" ] && [ ! -x "$VENV/bin/python" ]; then
    echo "  removing .venv - its interpreter is not executable on this machine"
    rm -rf "$VENV"
  fi

  [ -d "$VENV" ] || "$PYTHON" -m venv "$VENV"

  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV/bin/python" -m pip install -r "$BACKEND/requirements-dev.txt"

  # Never overwrite an existing .env; it holds real credentials.
  if [ ! -f "$BACKEND/.env" ]; then
    cp "$BACKEND/.env.example" "$BACKEND/.env"
    warn "created backend/.env from the example - fill DATABASE_URL, CLERK_SECRET_KEY and CLERK_ISSUER"
  fi

  bold "Frontend"
  (cd "$FRONTEND" && npm install)

  echo
  bold "Setup done. Next: ./scripts/dev.sh doctor"
}

build() {
  bold "Checks (no infrastructure required)"
  (cd "$BACKEND" && "$VENV/bin/python" tests/run_all.py)

  bold "Backend test suite"
  (cd "$BACKEND" && "$VENV/bin/python" -m pytest -q)

  bold "Frontend production bundle"
  (cd "$FRONTEND" && npm run build)

  echo
  bold "Build done. Bundle is in frontend/dist"
}

run() {
  doctor >/dev/null 2>&1 || { doctor; echo; bold "Refusing to start with blocking problems above"; exit 1; }

  bold "Applying migrations"
  (cd "$BACKEND" && "$VENV/bin/alembic" upgrade head)

  bold "Starting API on $API_HOST:$API_PORT and the dev server"

  (cd "$BACKEND" && "$VENV/bin/uvicorn" app.main:app --reload --host "$API_HOST" --port "$API_PORT") &
  local api=$!

  # The dev server proxies to the API, so it needs to know where it is when the
  # port is not the default.
  (cd "$FRONTEND" && VITE_DEV_API_TARGET="http://$API_HOST:$API_PORT" npm run dev) &
  local web=$!

  # Without this, stopping one process leaves the other holding its port.
  trap 'kill $api $web 2>/dev/null || true' INT TERM EXIT
  wait -n "$api" "$web"
}

case "${1:-doctor}" in
  doctor) doctor ;;
  setup)  setup ;;
  build)  build ;;
  run)    run ;;
  *)
    echo "usage: $0 {doctor|setup|build|run}" >&2
    exit 2
    ;;
esac
