#!/bin/sh
#
# Take the README's screenshots into assets/readme/*.webp. Drives a
# browser with web/scripts/demo/readme-shots.mts (Playwright: you sign
# in by hand in the window it opens, then it shoots headless), then
# converts each PNG with cwebp. Deliberately NOT a make target:
# re-taking the README assets is a rare, maintainer-only act.
#
# Read-only against the app: it creates and runs nothing. Set up what
# it shoots first (a signal whose log has agent columns, an agent, a
# sheet with a Send column) and pass their ids; see the .mts header for
# the env (SHOT_SIGNAL, SHOT_AGENT, SHOT_SEND_SHEET, SHOT_*_COLUMN,
# SHOT_DETAILS, SHOT_COOKIES, APP_URL). A shot whose id is unset is skipped, so one page can be
# re-taken alone.
#
# Needs node 22.6 or newer (runs the .mts directly), the web workspace
# installed (cd web && pnpm install; Chromium via
# `pnpm exec playwright install chromium`), and cwebp on PATH
# (brew install webp).
#
# POSIX sh, like the rest of scripts/.
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

OUT_DIR="${OUT_DIR:-assets/readme}"
# Lossy at a high quality: text stays crisp, a 2x shot lands in the low
# hundreds of KB.
WEBP_QUALITY="${WEBP_QUALITY:-90}"

for tool in node cwebp; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "error: $tool not found on PATH (see the header of this script)" >&2
        exit 1
    fi
done

WORK="$(mktemp -d)"
# The shots print their PNG paths on stdout and narrate on stderr. Run
# from web/ so node resolves playwright from the workspace.
if ! pngs="$(cd web && SHOT_OUT="$WORK" node scripts/demo/readme-shots.mts)"; then
    echo "error: the shots failed; anything taken is in $WORK" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"
for png in $pngs; do
    name="$(basename "$png" .png)"
    cwebp -quiet -q "$WEBP_QUALITY" "$png" -o "$OUT_DIR/$name.webp"
    echo "wrote $OUT_DIR/$name.webp ($(du -h "$OUT_DIR/$name.webp" | cut -f1))"
done
rm -rf "$WORK"
