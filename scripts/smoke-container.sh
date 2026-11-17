#!/usr/bin/env bash
# Smoke-check a built emu image the way the Server runs it: as user 10001, read-only, /tmp as a
# tmpfs, every capability dropped, next to a Postgres 18. Then import the SQLite test fixture
# inside the container, make the first Adult there, sign in, and check the pages show the data.
#
#   scripts/smoke-container.sh emu:test
#
# Needs docker and curl. Leaves nothing running (containers and network are removed on exit).
set -euo pipefail

image=${1:?usage: scripts/smoke-container.sh IMAGE}
port=${SMOKE_PORT:-18080}
name=emu-smoke-$$
here=$(cd "$(dirname "$0")/.." && pwd)

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- emu logs ---" >&2
    docker logs "$name-app" >&2 2>/dev/null || true
  fi
  docker rm -f "$name-app" "$name-pg" >/dev/null 2>&1 || true
  docker network rm "$name" >/dev/null 2>&1 || true
  rm -f "${jar:-}"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

uid=$(docker run --rm --entrypoint id "$image" -u)
[ "$uid" != 0 ] || fail "the image runs as root"
echo "ok: the image runs as uid $uid"

docker network create "$name" >/dev/null
docker run -d --name "$name-pg" --network "$name" \
  -e POSTGRES_USER=emu -e POSTGRES_PASSWORD=emu -e POSTGRES_DB=emu postgres:18 >/dev/null
for _ in $(seq 60); do docker exec "$name-pg" pg_isready -q -U emu -d emu && break; sleep 1; done
docker exec "$name-pg" pg_isready -q -U emu -d emu || fail "Postgres did not start"

docker run -d --name "$name-app" --network "$name" -p "127.0.0.1:$port:8080" \
  --user 10001 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  -e EMU_DATABASE_URL="postgresql://emu:emu@$name-pg:5432/emu" "$image" >/dev/null

base="http://localhost:$port"  # curl keeps Secure cookies over http only for localhost
jar=$(mktemp)
for _ in $(seq 60); do curl -fs -o /dev/null "$base/healthz" && break; sleep 0.5; done
[ "$(curl -fs "$base/healthz")" = ok ] || fail "/healthz did not answer ok"
echo "ok: /healthz"

# The importer runs inside the read-only container; the file goes in through stdin to /tmp.
docker exec -i "$name-app" python -c \
  "import sys; open('/tmp/emu.db', 'wb').write(sys.stdin.buffer.read())" \
  < "$here/tests/data/emu-sqlite.db"
docker exec "$name-app" python -m emu_the_menu.import_sqlite /tmp/emu.db | tail -1
echo "ok: imported the SQLite fixture inside the container"

# Signed out, pages go to sign-in; the sign-in page itself answers.
location=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "$base/")
[ "$location" = "303 $base/sign-in" ] || fail "signed out, / answered $location"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$base/sign-in")" = 200 ] || fail "no sign-in page"
echo "ok: signed out, / goes to the sign-in page, which answers"

password="smoke test password"
echo "$password" | docker exec -i "$name-app" \
  python -m emu_the_menu.accounts create-adult Alex --password-stdin
token=$(curl -fs -c "$jar" -b "$jar" "$base/sign-in" \
  | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1)
code=$(curl -s -o /dev/null -w '%{http_code}' -c "$jar" -b "$jar" \
  --data-urlencode name=Alex --data-urlencode "password=$password" \
  --data-urlencode "csrf_token=$token" "$base/sign-in")
[ "$code" = 303 ] || fail "signing in answered $code"
grep -q emu_session "$jar" || fail "no session cookie after signing in"
echo "ok: made the first Adult in the container and signed in"

for path in / /menu /menus/1 /recipes /eaters /occasions /settings /ingredients /export.json; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -b "$jar" "$base$path")
  [ "$code" = 200 ] || fail "$path answered $code"
done
echo "ok: every page answers 200"

curl -fs -b "$jar" "$base/" | grep -q "chicken thighs" \
  || fail "the Stock page does not show the imported stock"
curl -fs -b "$jar" "$base/recipes" | grep -q "Chicken and Rice" \
  || fail "the Recipes page does not show the imported recipes"
echo "ok: the imported data shows"

headers=$(curl -fs -D - -o /dev/null -b "$jar" "$base/")
grep -qi "^content-security-policy: default-src 'self'" <<<"$headers" || fail "no Content-Security-Policy"
! grep -qi '^server:' <<<"$headers" || fail "a Server header gives the software away"
echo "ok: Content-Security-Policy sent, no Server header"

if docker logs "$name-app" 2>&1 | grep -qiE 'traceback|error'; then
  fail "errors in the log"
fi
echo "ok: no errors in the log"
echo "PASS: $image"
