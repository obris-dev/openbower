#!/bin/sh
#
# Record assets/sheet-tour.gif: a sheet with two agent columns, rows
# landing, their cells resolving. Drives a headless browser with
# web/scripts/demo/sheet-tour.mts (Playwright; screens only, no browser
# chrome), then turns the video into a GIF with ffmpeg and gifski.
# Deliberately NOT a make target: re-recording the README asset is a
# rare, maintainer-only act, not dev loop.
#
# Needs, beyond a running local stack (make up):
# - node 22.6 or newer (runs the .mts directly) and the web workspace
#   installed (cd web && pnpm install; Playwright's Chromium via
#   `pnpm exec playwright install chromium`)
# - ffmpeg on PATH (brew install ffmpeg); gifski too if you want its
#   palette (brew install gifski), else ffmpeg's two-pass palette encode
#   is used
# - an account the app can sign in as, and a model the agents can reach
#
# A recording that already exists converts without re-running the tour
# (and re-spending the model): VIDEO=/path/to/recording.webm.
#
# Env: TOUR_EMAIL, TOUR_PASSWORD (required); TOUR_MODEL, TOUR_ROWS,
# TOUR_TOOLS, TOUR_SETTLE_MINUTES, TOUR_KEEP as the tour script
# documents. The agents use the contacts tool and web search through
# the vendors wired in config/tools.toml, which may be metered;
# TOUR_TOOLS=none is the dry run that spends nothing.
#
# POSIX sh, like the rest of scripts/.
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

OUT="${OUT:-assets/sheet-tour.gif}"
# GIF geometry: the README shows it at width 900; 12 fps keeps a
# minute-long research run under a few megabytes. GIF_SPEED is the
# time-lapse factor: the research phase is a minute or two of cells
# resolving, which reads better at a few times real time.
GIF_WIDTH="${GIF_WIDTH:-900}"
GIF_FPS="${GIF_FPS:-12}"
GIF_SPEED="${GIF_SPEED:-3}"

for tool in node ffmpeg; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "error: $tool not found on PATH (see the header of this script for what the recording needs)" >&2
        exit 1
    fi
done

WORK="$(mktemp -d)"

if [ -n "${VIDEO:-}" ]; then
    video="$VIDEO"
    if [ ! -f "$video" ]; then
        echo "error: VIDEO=$video is not a file" >&2
        exit 1
    fi
else
    if [ -z "${TOUR_EMAIL:-}" ] || [ -z "${TOUR_PASSWORD:-}" ]; then
        echo "error: TOUR_EMAIL and TOUR_PASSWORD are required (the account the tour signs in as)" >&2
        exit 1
    fi
    # The tour prints the video's path on stdout and narrates on stderr.
    # Run from web/ so node resolves playwright from the workspace. On a
    # failure the work directory is kept (the tour writes its diagnostics
    # there) and named, so the screenshot survives the exit.
    if ! video="$(cd web && TOUR_OUT="$WORK" node scripts/demo/sheet-tour.mts)"; then
        echo "error: the tour failed; its files are in $WORK" >&2
        exit 1
    fi
    if [ -z "$video" ] || [ ! -f "$video" ]; then
        echo "error: the tour produced no video" >&2
        exit 1
    fi
fi

# The recording is the expensive part (a model run per cell), so it is
# never thrown away over a conversion problem: the work directory is
# only cleaned once the GIF exists.
mkdir -p "$(dirname "$OUT")"
FILTERS="setpts=PTS/$GIF_SPEED,fps=$GIF_FPS,scale=$GIF_WIDTH:-1:flags=lanczos"
converted=0
if command -v gifski >/dev/null 2>&1; then
    # gifski dithers and palettes better than ffmpeg's own encoder,
    # which matters for a UI with thin borders and small type. It can
    # fail to even load when a Homebrew library it links moved, so a
    # failure falls through to ffmpeg rather than ending the run.
    mkdir -p "$WORK/frames"
    if ffmpeg -loglevel error -i "$video" -vf "$FILTERS" "$WORK/frames/%05d.png" \
        && gifski --fps "$GIF_FPS" --quality 90 -o "$OUT" "$WORK"/frames/*.png; then
        converted=1
    else
        echo "gifski could not encode (a broken install?); falling back to ffmpeg's palette encode" >&2
    fi
fi
if [ "$converted" -eq 0 ]; then
    # Two passes: a palette from the whole clip, then the encode with it.
    ffmpeg -loglevel error -y -i "$video" -vf "$FILTERS,palettegen=stats_mode=diff" "$WORK/palette.png"
    ffmpeg -loglevel error -y -i "$video" -i "$WORK/palette.png" \
        -lavfi "$FILTERS [x]; [x][1:v] paletteuse=dither=sierra2_4a" "$OUT"
fi

if [ ! -s "$OUT" ]; then
    echo "error: no GIF was written; the recording is still at $video" >&2
    exit 1
fi
rm -rf "$WORK"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
