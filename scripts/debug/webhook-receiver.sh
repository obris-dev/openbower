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
# Inside the stack a destination reaches it by container name, never by
# localhost (a loopback-published port is invisible from a container):
#   http://webhook-receiver:8080/<session id>
# The session id comes from the UI at http://localhost:8085.

set -eu

IMAGE="ghcr.io/tarampampam/webhook-tester:2"
NAME="webhook-receiver"
# The compose file pins the project name, so the default network is fixed;
# COMPOSE_PROJECT_NAME is honored for a checkout that overrides it.
NETWORK="${COMPOSE_PROJECT_NAME:-openbower}_default"
PORT="8085"

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

if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || true)" = "true" ]; then
    echo "$NAME is already running"
else
    docker pull "$IMAGE"
    docker run -d --rm --name "$NAME" --network "$NETWORK" -p "127.0.0.1:$PORT:8080" "$IMAGE" >/dev/null
    echo "$NAME started"
fi

cat <<EOF
  UI:          http://localhost:$PORT  (create a session there)
  destination: http://$NAME:8080/<session id>
  stop:        make receiver-stop
EOF
