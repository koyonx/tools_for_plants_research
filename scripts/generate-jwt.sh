#!/usr/bin/env bash
# Generate Supabase anon / service_role JWTs from a JWT_SECRET.
#
# Usage:
#   ./scripts/generate-jwt.sh <JWT_SECRET>
#
# Prints two lines to stdout (ANON_KEY=... / SERVICE_ROLE_KEY=...). Paste
# into .env.  Requires `openssl` and `jq`.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <JWT_SECRET>" >&2
  exit 1
fi

SECRET="$1"
NOW=$(date +%s)
EXP=$((NOW + 60 * 60 * 24 * 365 * 5))   # 5 years

b64url() {
  # Order sets so neither argument starts with `-` — keeps BSD tr (macOS) happy.
  openssl base64 -e -A | tr '/+' '_-' | tr -d '='
}

make_token() {
  local role="$1"
  local header payload signature
  header=$(printf '{"alg":"HS256","typ":"JWT"}' | b64url)
  # `jq -j` suppresses the trailing newline so it isn't b64-encoded into the
  # payload (which would both break strict JWT parsers and invalidate the HMAC
  # signature computed below).
  payload=$(jq -cjn \
      --arg role "$role" \
      --argjson iat "$NOW" \
      --argjson exp "$EXP" \
      '{role: $role, iss: "supabase", iat: $iat, exp: $exp}' | b64url)
  signature=$(printf '%s.%s' "$header" "$payload" \
      | openssl dgst -binary -sha256 -hmac "$SECRET" | b64url)
  printf '%s.%s.%s' "$header" "$payload" "$signature"
}

echo "ANON_KEY=$(make_token anon)"
echo "SERVICE_ROLE_KEY=$(make_token service_role)"
