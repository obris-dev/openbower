#!/bin/sh
# Build a web app the way its hosted worker is built and prove the worker
# serves. Run from the app's directory, which its `smoke` package script
# does, so `pnpm -r run smoke` checks every worker in the workspace and
# a package filter checks the changed ones:
#   worker-smoke.sh <path> [port]
#   worker-smoke.sh /login 8790
# The adapter's build runs the package's own `build` script (next build;
# the site copies the installer first), then patches Next's server for
# the Workers runtime. A Next the adapter does not know fails only when
# that server is constructed, which is the preview's first request and
# never the build: on 2026-10-22 a next bump green on every lane
# deployed a worker that threw on every request.
set -eu

path=${1:?usage: worker-smoke.sh <path> [port]}
port=${2:-8790}
name=${npm_package_name:-$(basename "$PWD")}

pnpm exec opennextjs-cloudflare build

log=$(mktemp)
pnpm exec opennextjs-cloudflare preview -- --port "$port" >"$log" 2>&1 &
pid=$!
# The preview is a process tree (pnpm, the adapter, wrangler, the
# runtime), and a signal to its root reaches none of the rest, so the
# whole tree is listed and killed together or the runtime is left
# holding the port.
tree() {
    echo "$1"
    for child in $(pgrep -P "$1"); do tree "$child"; done
}
# The status is taken before the teardown: the reaped preview's wait
# reports its signal, which under `set -e` would end the trap there and
# become the script's own status.
finish() {
    status=$?
    # shellcheck disable=SC2046
    kill $(tree "$pid") >/dev/null 2>&1
    wait "$pid" >/dev/null 2>&1 || true
    rm -f "$log"
    exit "$status"
}
trap finish EXIT

code=""
i=0
while [ $i -lt 60 ]; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port}${path}" || true)
    [ "$code" = 200 ] && break
    i=$((i + 1))
    sleep 1
done
if [ "$code" != 200 ]; then
    cat "$log"
    echo "${name} worker: ${path} answered '${code}'" >&2
    exit 1
fi
echo "${name} worker serves ${path}"
