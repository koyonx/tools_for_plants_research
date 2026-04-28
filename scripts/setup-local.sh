#!/usr/bin/env bash
# Bootstrap a fresh local .env with auto-generated secrets so that
# `docker compose up` works on the first try.  Idempotent: if `.env`
# already exists it leaves your secrets alone (override with
# `FORCE=1 ./scripts/setup-local.sh`).
#
# What this does:
#   1. Verify the host has docker, openssl, jq.
#   2. Copy `.env.example` -> `.env`.
#   3. Generate random secrets:
#        - JWT_SECRET           (48 base64 bytes)
#        - POSTGRES_PASSWORD    (24 base64 bytes, alnum-only)
#        - DASHBOARD_PASSWORD   (24 base64 bytes, alnum-only)
#   4. Derive ANON_KEY / SERVICE_ROLE_KEY from JWT_SECRET via
#      ./scripts/generate-jwt.sh.
#   5. Mirror ANON_KEY into NEXT_PUBLIC_SUPABASE_ANON_KEY.
#   6. Print next steps.
#
# After running this, `make up` should bring the whole stack up
# without further manual editing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
ENV_EXAMPLE="$REPO_ROOT/.env.example"
GEN_JWT="$REPO_ROOT/scripts/generate-jwt.sh"

green()  { printf "\033[1;32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[1;33m%s\033[0m\n" "$*"; }
red()    { printf "\033[1;31m%s\033[0m\n" "$*" >&2; }

# 1. Tool prerequisites ---------------------------------------------------
missing=()
for cmd in docker openssl jq; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if (( ${#missing[@]} > 0 )); then
  red "missing required commands: ${missing[*]}"
  red "install them and re-run.  on macOS:  brew install ${missing[*]}"
  exit 1
fi

# Confirm Docker daemon is responsive — `docker info` blocks if the
# Desktop VM is paused / not started, which is a much better failure
# message than the generic compose error you'd otherwise hit.
if ! docker info >/dev/null 2>&1; then
  red "Docker is installed but the daemon is not responding."
  red "Open Docker Desktop and wait until it says 'Engine running', then re-run."
  exit 1
fi

# 2. Copy .env.example -> .env -------------------------------------------
if [[ -f "$ENV_FILE" && "${FORCE:-0}" != "1" ]]; then
  yellow ".env already exists — keeping existing secrets."
  yellow "(set FORCE=1 to overwrite, e.g. 'FORCE=1 $0')"
else
  if [[ ! -f "$ENV_EXAMPLE" ]]; then
    red ".env.example missing — run from a clean checkout."
    exit 1
  fi
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  green "wrote $ENV_FILE from .env.example"
fi

# Helper — sed -i works differently on BSD (macOS) vs GNU.  Use a
# tmpfile-based replace that is portable across both.
replace_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    awk -v k="$key" -v v="$value" '
      BEGIN {FS = OFS = "="}
      $1 == k { print k "=" v; next }
      { print }
    ' "$ENV_FILE" >"$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

# Read current .env value (empty if unset / file missing).
current_env() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null \
    | head -1 \
    | cut -d= -f2- \
    || true
}

# Generate `n` bytes of base64 with `+/` stripped (alnum-friendly for
# Postgres roles + dashboard creds — Kong barfs on `/` in passwords).
gen_secret() {
  local n="$1"
  openssl rand -base64 "$n" | tr -d '\n+/=' | head -c "$n"
}

# 3-5. Generate / fill secrets -------------------------------------------
PLACEHOLDER_HINT="replace-with"   # both example values start with this

needs_jwt_secret=false
current_jwt_secret=$(current_env JWT_SECRET || true)
if [[ -z "$current_jwt_secret" || "$current_jwt_secret" == *"$PLACEHOLDER_HINT"* ]]; then
  needs_jwt_secret=true
fi

if $needs_jwt_secret; then
  jwt_secret=$(openssl rand -base64 48 | tr -d '\n')
  replace_env JWT_SECRET "$jwt_secret"
  green "generated JWT_SECRET (48 random bytes, base64)"
else
  jwt_secret="$current_jwt_secret"
  yellow "kept existing JWT_SECRET"
fi

# Re-derive anon / service tokens from whatever JWT_SECRET we ended up with.
# generate-jwt.sh prints two lines like `ANON=...` and `SERVICE=...`.
chmod +x "$GEN_JWT" 2>/dev/null || true
jwt_out=$("$GEN_JWT" "$jwt_secret")
anon_key=$(printf '%s\n' "$jwt_out" | awk -F= '/^ANON_KEY=/   {print $2}')
service_key=$(printf '%s\n' "$jwt_out" | awk -F= '/^SERVICE_ROLE_KEY=/ {print $2}')
if [[ -z "$anon_key" || -z "$service_key" ]]; then
  red "generate-jwt.sh did not produce both ANON_KEY and SERVICE_ROLE_KEY"
  red "raw output was:"
  printf '%s\n' "$jwt_out" >&2
  exit 1
fi
replace_env ANON_KEY "$anon_key"
replace_env SERVICE_ROLE_KEY "$service_key"
replace_env NEXT_PUBLIC_SUPABASE_ANON_KEY "$anon_key"
green "derived ANON_KEY / SERVICE_ROLE_KEY (5-year expiry)"
green "mirrored ANON_KEY -> NEXT_PUBLIC_SUPABASE_ANON_KEY"

# Postgres password — only generate if still placeholder.
current_pgpass=$(current_env POSTGRES_PASSWORD || true)
if [[ -z "$current_pgpass" || "$current_pgpass" == *"$PLACEHOLDER_HINT"* ]]; then
  pgpass=$(gen_secret 24)
  replace_env POSTGRES_PASSWORD "$pgpass"
  green "generated POSTGRES_PASSWORD (24 alnum chars)"
else
  yellow "kept existing POSTGRES_PASSWORD"
fi

# Dashboard (Studio) password — same rule.
current_dashpass=$(current_env DASHBOARD_PASSWORD || true)
if [[ -z "$current_dashpass" || "$current_dashpass" == "replace-me" ]]; then
  dashpass=$(gen_secret 24)
  replace_env DASHBOARD_PASSWORD "$dashpass"
  green "generated DASHBOARD_PASSWORD (24 alnum chars)"
else
  yellow "kept existing DASHBOARD_PASSWORD"
fi

# 6. Final hint -----------------------------------------------------------
echo
green "==> .env is ready."
cat <<'EOM'

Next steps:

  make up        # build + start the whole stack (first run pulls a few GB)
  make smoke     # poll /health + /analyze/segformer/status until ready
  open http://localhost:3000     # the app
  open http://localhost:3001     # Supabase Studio
                                  # user: supabase (set in .env: DASHBOARD_USERNAME)
                                  # pass: see DASHBOARD_PASSWORD in .env

To reset everything (drop DB + caches):  make down-volumes
EOM
