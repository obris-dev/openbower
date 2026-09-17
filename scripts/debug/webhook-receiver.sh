#!/bin/sh
#
# A throwaway webhook receiver with a UI, for watching what a destination
# actually sends (headers, signature, body) while developing locally. It is
# a debugging aid, NOT part of the compose stack: the stack must not carry
# a service the product never needs, so this runs as a plain container
# joined to the stack's network and is gone on `stop`.
#
# Usage:
#   ./scripts/debug/webhook-receiver.sh          # pull + start (idempotent)
#   ./scripts/debug/webhook-receiver.sh stop
#
# ONE URL works from the browser and from inside the stack:
#   http://webhook-receiver.localhost:8085/<session id>
# Inside the stack the name is a Docker network alias (localhost there
# would be the core container's own loopback). On the host, Chromium and
# Firefox resolve any *.localhost name to loopback by themselves, and so
# do macOS and systemd-resolved Linux system-wide. Where the system
# resolver does not, `start` says which hosts file needs the one line;
# it never edits the file.

set -eu

IMAGE="ghcr.io/tarampampam/webhook-tester:2"
NAME="webhook-receiver"
# A dotted name, because a destination URL must carry a full host name
# (the URL validator rejects a bare container name). The UI advertises it
# too, so a URL copied there is a working destination as-is.
# Overridable, which is also how the unresolved-name path is exercised.
HOST="${WEBHOOK_RECEIVER_HOST:-webhook-receiver.localhost}"
# The compose file pins the project name, so the default network is fixed;
# COMPOSE_PROJECT_NAME is honored for a checkout that overrides it.
NETWORK="${COMPOSE_PROJECT_NAME:-openbower}_default"
# The same port inside and out, so the one URL holds on both sides.
PORT="8085"
# Sessions live on a named volume: a destination URL embeds its session
# id, so an in-memory store would orphan every destination on restart.
# `stop` leaves the volume; `docker volume rm` it to start clean.
VOLUME="webhook-receiver-data"

# Where the machine's own resolver reads static names. Under WSL or Git
# Bash the browser is on Windows, so the Windows file is the one that
# matters.
HOSTS_FILE="/etc/hosts"
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) HOSTS_FILE="C:\\Windows\\System32\\drivers\\etc\\hosts" ;;
    Linux)
        if grep -qi microsoft /proc/version 2>/dev/null; then
            HOSTS_FILE="C:\\Windows\\System32\\drivers\\etc\\hosts (the Windows file, not WSL's)"
        fi
        ;;
esac

answers() {
    curl -fsS -o /dev/null --max-time 2 "http://$1:$PORT/api/version" 2>/dev/null
}

# The receiver needs a moment after `docker run`; loopback proves it is up
# before the name is judged, so a slow start never reads as a DNS problem.
wait_until_up() {
    i=0
    while [ "$i" -lt 20 ]; do
        if answers 127.0.0.1; then return 0; fi
        i=$((i + 1))
        sleep 0.5
    done
    echo "$NAME did not answer on 127.0.0.1:$PORT after 20 tries: docker logs $NAME" >&2
    return 1
}

report_host_resolution() {
    if answers "$HOST"; then return 0; fi
    cat <<EOF
$HOST does not resolve on this machine (the receiver answers on 127.0.0.1:$PORT).
Chrome and Firefox open it anyway; curl and Safari need this line in $HOSTS_FILE:
  127.0.0.1 $HOST
EOF
}

case "${1:-start}" in
    stop)
        docker rm -f "$NAME" >/dev/null 2>&1 && echo "$NAME stopped" || echo "$NAME was not running"
        exit 0
        ;;
    start) ;;
    *)
        echo "usage: $0 [start|stop]" >&2
        exit 1
        ;;
esac

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
    echo "network $NETWORK is missing: run make up first (the receiver joins the stack's network)" >&2
    exit 1
fi

run_receiver() {
    docker run -d --name "$NAME" --network "$NETWORK" --network-alias "$HOST" -p "127.0.0.1:$PORT:$PORT" \
        -v "$VOLUME:/data" -e STORAGE_DRIVER=fs -e FS_STORAGE_DIR=/data \
        -e HTTP_PORT="$PORT" -e PUBLIC_URL_ROOT="http://$HOST:$PORT" "$IMAGE" >/dev/null
    echo "$NAME started"
}

# No --rm: a container that died keeps its logs until the next start
# shows them, so an unexplained exit is not also an unexplainable one.
state="$(docker inspect -f '{{.State.Status}}' "$NAME" 2>/dev/null || true)"
[ -n "$state" ] || state="absent"
case "$state" in
    running)
        echo "$NAME is already running"
        ;;
    absent)
        docker pull "$IMAGE"
        run_receiver
        ;;
    *)
        echo "$NAME had exited ($state); its last lines:"
        docker logs --tail 5 "$NAME" 2>&1 | sed 's/^/    /'
        docker rm "$NAME" >/dev/null
        run_receiver
        ;;
esac

wait_until_up
report_host_resolution

cat <<EOF
  UI:          http://$HOST:$PORT  (create a session there)
  destination: http://$HOST:$PORT/<session id>  (the URL the UI shows)
  stop:        make receiver-stop
EOF
