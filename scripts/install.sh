#!/bin/sh
#
# OpenBower's one-command install, runnable straight from a fresh machine:
#
#   curl -fsSL https://openbower.ai | sh
#
# Clones the newest release into a directory of your choice and hands off
# to the operator command inside it, scripts/bower-admin.sh, whose
# `install` runs the prerequisites checklist, builds and starts the
# stack, sets up an inference source, and puts `bower-admin` on your
# PATH. OpenBower is bring-your-own-model: nothing here downloads one.
#
# Pre-clone, the only tool this needs is git; the full checklist (Docker,
# Compose, a running daemon) runs right after the clone, in one place.
# Guide-only: it never installs a tool for you, it says how.
#
# POSIX sh, no bashisms (shellcheck -s sh in CI), so the whole
# `curl | sh` flow is bash-free.
#
# Env overrides go on the `sh`, not the `curl` (the assignment binds to
# the command it precedes), e.g.  curl -fsSL https://openbower.ai | OPENBOWER_BRANCH=main sh
#   OPENBOWER_DIR     where to clone (default ./openbower under the current
#                     directory; asked when a terminal is attached)
#   OPENBOWER_BRANCH  branch or tag to install (default: the newest
#                     published release, falling back to main)
#   OPENBOWER_SSH=1   clone over SSH instead of HTTPS
#   OPENBOWER_REPO    clone from this URL or local path instead (a fork,
#                     or a checkout on this machine)
set -eu

readonly REPO_HTTPS="https://github.com/obris-dev/openbower.git"
readonly REPO_SSH="git@github.com:obris-dev/openbower.git"
readonly REPO_SLUG="obris-dev/openbower"
OPENBOWER_DIR="${OPENBOWER_DIR:-}"
OPENBOWER_BRANCH="${OPENBOWER_BRANCH:-}"

if [ -t 1 ]; then
    BOLD=$(printf '\033[1m'); RED=$(printf '\033[0;31m'); GREEN=$(printf '\033[0;32m'); NC=$(printf '\033[0m')
else
    BOLD=''; RED=''; GREEN=''; NC=''
fi
info() { printf '%s\n' "  $1"; }
step() { printf '%s\n' "${BOLD}$1${NC}"; }
ok()   { printf '%s\n' "  ${GREEN}+${NC} $1"; }
fail() { printf '\n%s\n\n' "  ${RED}x $1${NC}" >&2; exit 1; }

# The package manager's install line for git, by distro family from
# /etc/os-release (ID, then ID_LIKE), so the hint is one a person can
# paste rather than "use your package manager".
git_hint() {
    case "$(uname -s)" in
        Darwin) printf '%s' "git: xcode-select --install (or brew install git)"; return 0 ;;
        Linux) ;;
        *) printf '%s' "git: https://git-scm.com/downloads"; return 0 ;;
    esac
    # shellcheck disable=SC1091  # the system's own file, read for two fields
    _ids="$( (. /etc/os-release 2>/dev/null && printf '%s %s' "${ID:-}" "${ID_LIKE:-}") )"
    case " $_ids " in
        *" debian "* | *" ubuntu "*) printf '%s' "git: sudo apt-get update && sudo apt-get install -y git" ;;
        *" fedora "* | *" rhel "* | *" centos "*) printf '%s' "git: sudo dnf install -y git" ;;
        *" arch "*) printf '%s' "git: sudo pacman -S --noconfirm git" ;;
        *" alpine "*) printf '%s' "git: sudo apk add git" ;;
        *" suse "* | *" opensuse "*) printf '%s' "git: sudo zypper install -y git" ;;
        *) printf '%s' "git: your distro's package manager, or https://git-scm.com/downloads" ;;
    esac
}

resolve_target_dir() {
    # A named subdirectory of the CURRENT directory by default (never
    # cwd itself, which could be non-empty). Asked only when someone is
    # plausibly watching: stdout is a terminal (still true under curl |
    # sh, where only stdin is the pipe) AND the controlling tty is
    # readable; a readable /dev/tty alone would block the read forever.
    _default="$(pwd)/openbower"
    if [ -z "$OPENBOWER_DIR" ]; then
        if [ -t 1 ] && [ -r /dev/tty ]; then
            printf '  Install into [%s]: ' "$_default" > /dev/tty
            IFS= read -r _reply < /dev/tty || _reply=""
            OPENBOWER_DIR="${_reply:-$_default}"
        else
            OPENBOWER_DIR="$_default"
        fi
    fi
    # A leading ~/ is expanded by hand (the ${x/#~/} form is a bashism);
    # the quoted tilde is the literal we match and strip.
    # shellcheck disable=SC2088
    case "$OPENBOWER_DIR" in
        "~/"*) OPENBOWER_DIR="$HOME/${OPENBOWER_DIR#"~/"}" ;;
    esac
    case "$OPENBOWER_DIR" in
        /*) ;;
        *) OPENBOWER_DIR="$(pwd)/$OPENBOWER_DIR" ;;
    esac
}

# The newest stable release tag (vMAJOR.MINOR.PATCH, newest first in the
# API's list, prereleases rejected by the anchor), so a fresh install
# lands on a release while main keeps moving. No jq on a fresh box. Falls
# back to main when there is no release yet or the lookup fails.
resolve_ref() {
    [ -n "$OPENBOWER_BRANCH" ] && return 0
    if command -v curl >/dev/null 2>&1; then
        OPENBOWER_BRANCH="$(curl -fsSL "https://api.github.com/repos/$REPO_SLUG/releases?per_page=100" 2>/dev/null \
            | grep -oE '"tag_name"[[:space:]]*:[[:space:]]*"[^"]+"' \
            | sed -E 's/.*"([^"]+)"$/\1/' \
            | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
            | head -n1)"
    fi
    if [ -n "$OPENBOWER_BRANCH" ]; then
        info "Installing the newest release: $OPENBOWER_BRANCH"
    else
        OPENBOWER_BRANCH="main"
        info "No published release found; installing from main"
    fi
}

fetch_repo() {
    step "Fetching OpenBower"
    if ! command -v git >/dev/null 2>&1; then
        info "Install $(git_hint), then re-run."
        fail "git is required to fetch OpenBower"
    fi
    _url="$REPO_HTTPS"
    [ -n "${OPENBOWER_SSH:-}" ] && _url="$REPO_SSH"
    [ -n "${OPENBOWER_REPO:-}" ] && _url="$OPENBOWER_REPO"
    if [ -d "$OPENBOWER_DIR/.git" ]; then
        info "Updating the existing checkout at $OPENBOWER_DIR"
        git -C "$OPENBOWER_DIR" fetch --quiet --tags origin || fail "git fetch failed"
        git -C "$OPENBOWER_DIR" checkout --quiet "$OPENBOWER_BRANCH" || fail "git checkout $OPENBOWER_BRANCH failed"
        git -C "$OPENBOWER_DIR" pull --quiet --ff-only origin "$OPENBOWER_BRANCH" 2>/dev/null || true
    elif [ -d "$OPENBOWER_DIR" ] && [ -n "$(ls -A "$OPENBOWER_DIR" 2>/dev/null)" ]; then
        # Non-empty but not a checkout: the leftover of an attempt that
        # did not finish. A prior run may have started containers under
        # the compose project, so they go first or a re-clone silently
        # reattaches the old volume.
        info "$OPENBOWER_DIR exists and is not an OpenBower checkout (a previous attempt?). Clean up, then re-run:"
        info "  docker compose -p openbower down -v    # if a prior run started the stack"
        info "  rm -rf \"$OPENBOWER_DIR\""
        info "Or set OPENBOWER_DIR to a fresh path."
        fail "target directory is not empty"
    else
        info "Cloning into $OPENBOWER_DIR"
        # A bad ref fails loudly rather than silently cloning the default
        # branch under a misleading name.
        git clone --quiet --branch "$OPENBOWER_BRANCH" "$_url" "$OPENBOWER_DIR" \
            || fail "git clone failed (does branch/tag '$OPENBOWER_BRANCH' exist, and can this machine reach GitHub?)"
    fi
    ok "Source at $OPENBOWER_DIR"
}

# Wrapped in a function so `curl | sh` reads the whole script before it
# runs any of it: a truncated download then runs nothing.
main() {
    echo ""
    step "OpenBower install"
    info "https://openbower.ai"
    echo ""
    resolve_target_dir
    resolve_ref
    fetch_repo
    cd "$OPENBOWER_DIR"
    # Tells the checklist which re-run line to print on a miss.
    export OPENBOWER_VIA_CURL=1
    # `sh <script>`, not `./<script>`: the handoff must not depend on the
    # +x bit surviving the clone.
    exec sh ./scripts/bower-admin.sh install
}

main "$@"
