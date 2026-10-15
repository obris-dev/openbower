#!/bin/sh
# Start a built image and prove it serves, the same check for CI's images
# job and `make test-images`:
#   image-smoke.sh core <image>          the core answers Django's system check
#   image-smoke.sh web <image> [port]    the web serves /login and sends a
#                                        signed-out browser to its backend
# The web's redirect is where its build arguments are proven: a build
# that was not given its backend origin answers 500 there with the app's
# own refusal, while the login page renders either way.
set -eu

kind=${1:?usage: image-smoke.sh core|web <image> [port]}
image=${2:?usage: image-smoke.sh core|web <image> [port]}
port=${3:-3003}

case $kind in
core)
    # The test profile seeds every setting but the secret, so the check
    # runs with no database and no other service.
    docker run --rm -e DJANGO_ENV=test -e DJANGO_SECRET_KEY=test-only "$image" \
        uv run --no-sync python apps/core/manage.py check
    ;;
web)
    name="openbower-web-smoke-$$"
    docker run -d --name "$name" -p "127.0.0.1:${port}:3003" "$image" >/dev/null
    trap 'docker rm -f "$name" >/dev/null 2>&1' EXIT
    code=""
    i=0
    while [ $i -lt 30 ]; do
        code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port}/login" || true)
        [ "$code" = 200 ] && break
        i=$((i + 1))
        sleep 1
    done
    if [ "$code" != 200 ]; then
        docker logs "$name"
        echo "/login answered '${code}'" >&2
        exit 1
    fi
    kickoff=$(curl -s -o /dev/null -w '%{redirect_url}' "http://127.0.0.1:${port}/")
    if [ "$kickoff" != "http://localhost:8002/v1/auth/login" ]; then
        docker logs "$name"
        echo "signed-out / went to '${kickoff}'" >&2
        exit 1
    fi
    echo "web image serves /login and bounces a signed-out browser to its backend"
    ;;
*)
    echo "unknown kind '${kind}': core or web" >&2
    exit 2
    ;;
esac
