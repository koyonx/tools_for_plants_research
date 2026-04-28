#!/usr/bin/env bash
# Print a working dev sign-in URL for the given email.
#
# We can't reuse the email-template magic link directly: the frontend uses
# @supabase/ssr (PKCE), which expects a `?code=` callback that only the
# original signInWithOtp flow can produce.  Instead we ask GoTrue's admin
# API for a one-shot token and route it through /auth/dev-login, which
# calls verifyOtp({token_hash}) server-side and sets the session cookie.
set -euo pipefail

email="${1:-}"
next="${2:-/dashboard}"
if [[ -z "$email" ]]; then
  echo "usage: $0 <email> [next-path]" >&2
  exit 1
fi

env_file="$(cd "$(dirname "$0")/.." && pwd)/.env"
# Anchor on `=` so we don't pick up `SERVICE_ROLE_KEY_FOO=...`, and strip
# any trailing \r in case `.env` was edited on Windows — curl would
# otherwise send the CR as part of the bearer token and get 401.
service_key="$(grep -E '^SERVICE_ROLE_KEY=' "$env_file" | head -n1 | cut -d= -f2- | tr -d '\r')"
api="http://localhost:8000"
app="http://localhost:3000"

resp="$(curl -fsS -X POST "$api/auth/v1/admin/generate_link" \
  -H "apikey: $service_key" -H "Authorization: Bearer $service_key" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"magiclink\",\"email\":\"$email\"}")"

token_hash="$(printf '%s' "$resp" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(d.get('hashed_token',''))")"

if [[ -z "$token_hash" ]]; then
  echo "failed to extract hashed_token from response:" >&2
  echo "$resp" >&2
  exit 1
fi

next_enc="$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$next")"
echo "$app/auth/dev-login?token_hash=$token_hash&next=$next_enc"
