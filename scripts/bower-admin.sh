#!/bin/sh
#
# bower-admin: the operator command for a self-hosted OpenBower. One
# script for the whole lifecycle (install, up, stop, upgrade, the model
# setup, the logs), so running the product never needs make.
#
# The installer (scripts/install.sh, the `curl | sh` entry) hands off to
# `install` here right after the clone, and writes a shim named
# bower-admin on the PATH that execs THIS file inside the checkout. So
# the command rides the checkout: `bower-admin upgrade` advances the
# checkout and upgrades the command with it, and there is nothing
# separate to update. Runnable directly from a clone as well:
#   sh scripts/bower-admin.sh install
#
# POSIX sh, no bashisms (shellcheck -s sh in CI), so the whole install
# path is bash-free.
#
# Env:
#   OPENBOWER_BRANCH   upgrade target: a branch or tag (default: the
#                      newest published release tag, falling back to main)
#   OPENBOWER_BIN_DIR  where install writes the shim (default ~/.local/bin)
#   OPENBOWER_NO_MODIFY_PATH=1
#                      never touch a shell rc file; install prints the
#                      PATH line to add by hand instead
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# The compose project is PINNED, and passed as well as exported, so a
# COMPOSE_PROJECT_NAME in the environment can never split one checkout
# into two projects with their own containers and volumes.
PROJECT=openbower
export COMPOSE_PROJECT_NAME="$PROJECT"
REPO_SLUG="obris-dev/openbower"
NETWORK=openbower-suite
# The services built from the core image: every one reads
# config/providers.toml at boot, so a setup edit restarts exactly these.
CORE_SERVICES="core jobs fill-provisioner fill-consumer autofill-provisioner autofill-consumer preview-consumer ingest-worker cron"
# The SERIAL cold path on a first start: the setup one-shot syncs its
# venv, then core and the workers sync theirs, then the web installs its
# dependencies and compiles its first routes.
WAIT_TIMEOUT=900
COMPOSE_MIN_MAJOR=2
COMPOSE_MIN_MINOR=24
CORE_IMAGE=openbower-core:local
PROVIDERS=config/providers.toml
PROVIDERS_TEMPLATE=config/templates/providers.example.toml
TOOLS=config/tools.toml
TOOLS_TEMPLATE=config/templates/tools.example.toml
CORE_ENV=apps/core/.env
BIN_DIR="${OPENBOWER_BIN_DIR:-$HOME/.local/bin}"
APP_URL="http://localhost:3003"
# Ollama's default endpoint, the menu's first suggestion for a server
# you run (rewritten to the host gateway for the containers).
OLLAMA_URL="http://localhost:11434/v1"

# Colors only when stdout is a terminal (curl | sh keeps stdout on the
# tty). printf, not echo: echo mangles a message that starts with -n
# or carries a backslash.
# The palette: bold for a step heading, green + done, yellow ! warning,
# red x failure, bold cyan for anything a person copies (a command, a
# path, a URL, a value), prose plain.
if [ -t 1 ]; then
    BOLD=$(printf '\033[1m'); RED=$(printf '\033[0;31m'); GREEN=$(printf '\033[0;32m'); YELLOW=$(printf '\033[1;33m'); CYAN=$(printf '\033[1;36m'); NC=$(printf '\033[0m')
else
    BOLD=''; RED=''; GREEN=''; YELLOW=''; CYAN=''; NC=''
fi
info() { printf '%s\n' "  $1"; }
step() { printf '\n%s\n' "${BOLD}$1${NC}"; }
ok()   { printf '%s\n' "  ${GREEN}+${NC} $1"; }
warn() { printf '%s\n' "  ${YELLOW}!${NC} $1"; }
fail() { printf '\n%s\n\n' "  ${RED}x $1${NC}" >&2; exit 1; }

compose() { docker compose -p "$PROJECT" "$@"; }

# Someone is plausibly watching: stdout is a terminal AND the controlling
# tty is readable. Both, because a readable /dev/tty alone (CI with a pty,
# a detached tmux) would block a read forever.
has_tty() { [ -t 1 ] && [ -r /dev/tty ]; }

# Prompt on the tty and echo the reply (empty on EOF). The prompt goes to
# the tty, not stdout, so a captured call gets only the answer.
ask() {
    printf '%s' "$1" > /dev/tty
    IFS= read -r _reply < /dev/tty || _reply=""
    printf '%s' "$_reply"
}

# Same, for a key: echoes one * per character so a paste visibly
# landed, handles backspace, and never shows the key. The terminal is
# put in raw mode for the read and restored after, on Ctrl-C too.
ask_secret() {
    printf '%s' "$1" > /dev/tty
    _saved="$(stty -g < /dev/tty)"
    trap 'stty "$_saved" < /dev/tty; printf "\n" > /dev/tty; exit 130' INT
    stty -echo -icanon min 1 time 0 < /dev/tty
    _reply=""
    while :; do
        # One byte; the substitution strips a newline, so Enter (and
        # EOF) read as empty and end the input.
        _ch="$(dd bs=1 count=1 < /dev/tty 2>/dev/null)"
        case "$_ch" in
            '' | "$(printf '\004')") break ;;
            "$(printf '\177')" | "$(printf '\010')")
                if [ -n "$_reply" ]; then
                    _reply="${_reply%?}"
                    printf '\b \b' > /dev/tty
                fi
                ;;
            # Ctrl-U and Ctrl-W: clear the lot (a key has no words), so
            # a botched paste is one key to redo.
            "$(printf '\025')" | "$(printf '\027')")
                while [ -n "$_reply" ]; do
                    _reply="${_reply%?}"
                    printf '\b \b' > /dev/tty
                done
                ;;
            # An arrow or function key arrives as ESC [ ... <letter>;
            # swallow the sequence so its tail is not taken as input.
            "$(printf '\033')")
                _ch="$(dd bs=1 count=1 < /dev/tty 2>/dev/null)"
                case "$_ch" in
                    '[' | 'O')
                        while :; do
                            _ch="$(dd bs=1 count=1 < /dev/tty 2>/dev/null)"
                            case "$_ch" in '' | [@A-Za-z~]) break ;; esac
                        done
                        ;;
                esac
                ;;
            [[:cntrl:]]) ;;
            *)
                _reply="$_reply$_ch"
                printf '*' > /dev/tty
                ;;
        esac
    done
    stty "$_saved" < /dev/tty
    trap - INT
    printf '\n' > /dev/tty
    printf '%s' "$_reply"
}

# Lock a config file that holds secrets as tightly as the containers can
# bear: they run as uid 1000 and read these files through the bind
# mount, so owner-only is right on Docker Desktop (which maps the user)
# and when the installing user IS uid 1000, and would make the app
# refuse to boot for any other Linux user. Those keep the umask's read
# bits; the checkout sits in their own home directory.
lock_file() {
    if [ "$(uname -s)" = Darwin ] || [ "$(id -u)" = 1000 ]; then
        chmod 600 "$1" 2>/dev/null || true
    else
        chmod 644 "$1" 2>/dev/null || true
    fi
}

# Set KEY=$1 to VALUE=$2 in env file $3 through a temp file and one mv:
# any existing uncommented assignment is dropped, a commented default
# line stays. The file can hold secrets, so it is locked owner-only on
# every write, not just at one caller.
env_set() {
    _tmp="$3.tmp.$$"
    grep -v "^$1=" "$3" > "$_tmp" || true
    printf '%s=%s\n' "$1" "$2" >> "$_tmp"
    lock_file "$_tmp"
    mv "$_tmp" "$3"
}

# ---------------------------------------------------------------- seeding

# Seed a file from its template ONLY when absent: a pull that touched the
# template must never overwrite an operator's real keys.
seed_file() {
    [ -e "$1" ] && return 0
    cp "$2" "$1"
    ok "seeded $1 from $2$3"
}

seed_config() {
    seed_file .env .env.example " (signs in at the hosted hub)"
    if [ ! -e "$CORE_ENV" ]; then
        cp apps/core/.env.example "$CORE_ENV"
        # The template's secret is a dev placeholder; an install gets
        # its own. 32 random bytes as hex, with no tool beyond od.
        _secret="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        env_set DJANGO_SECRET_KEY "$_secret" "$CORE_ENV"
        ok "seeded $CORE_ENV with a generated DJANGO_SECRET_KEY"
    fi
    seed_file "$TOOLS" "$TOOLS_TEMPLATE" " (web search through DuckDuckGo; edit it for metered vendors)"
    # config/providers.toml is deliberately NOT seeded here: `setup` owns
    # it, so its absence is the one fact that says setup has not run.
}

# The cross-stack network the hosted services' development copies share
# with this stack. Idempotent; created by whichever stack comes up first.
ensure_network() {
    docker network inspect "$NETWORK" >/dev/null 2>&1 && return 0
    docker network create "$NETWORK" >/dev/null 2>&1 \
        || docker network inspect "$NETWORK" >/dev/null 2>&1 \
        || fail "could not create the $NETWORK docker network"
}

core_running() {
    compose ps --status running --services 2>/dev/null | grep -qx core
}

# ---------------------------------------------------------------- doctor

compose_version() {
    docker compose version --short 2>/dev/null | grep -oE '^v?[0-9]+\.[0-9]+' | sed 's/^v//'
}

# True only when a version parsed AND it is below the floor: an
# unparseable version never blocks a run on a guess.
compose_too_old() {
    _ver="$(compose_version)"
    _maj="${_ver%%.*}"
    _min="${_ver#*.}"
    case "$_maj" in '' | *[!0-9]*) return 1 ;; esac
    case "$_min" in '' | *[!0-9]*) _min=0 ;; esac
    [ "$_maj" -lt "$COMPOSE_MIN_MAJOR" ] || { [ "$_maj" -eq "$COMPOSE_MIN_MAJOR" ] && [ "$_min" -lt "$COMPOSE_MIN_MINOR" ]; }
}

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

# Docker's own install script covers the common distros (Debian,
# Ubuntu, Fedora, CentOS, RHEL); the group step is what lets the
# install run without sudo, and it takes a new login to apply.
docker_hint() {
    case "$(uname -s)" in
        Darwin) printf '%s' "Docker Desktop: https://docs.docker.com/desktop/install/mac-install/ (or brew install --cask docker), then launch it." ;;
        Linux)  printf '%s' "Docker Engine + Compose: curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker \$USER, then log out and back in (other distros: https://docs.docker.com/engine/install/)" ;;
        *)      printf '%s' "Docker: https://docs.docker.com/get-docker/" ;;
    esac
}

# The prerequisites checklist: every check prints its line at once (the
# list reads top to bottom), the misses are collected, and they print
# together with how to fix each, so nothing dies one command deep.
cmd_doctor() {
    _fixes=""
    _os="$(uname -s)"
    pass() { printf '%s\n' "  ${GREEN}+${NC} $1"; }
    miss() { printf '%s\n' "  ${RED}-${NC} ${RED}$1${NC}"; _fixes="${_fixes}  $2
"; }
    step "Checking prerequisites"
    case "$_os" in
        Darwin | Linux) pass "OS: $_os" ;;
        *) miss "OS: $_os (need macOS or Linux)" "On Windows, run the install inside WSL2: https://learn.microsoft.com/windows/wsl/install" ;;
    esac
    if command -v git >/dev/null 2>&1; then
        pass "git"
    else
        miss "git" "$(git_hint)"
    fi
    if ! command -v docker >/dev/null 2>&1; then
        miss "Docker" "$(docker_hint)"
    elif ! docker compose version >/dev/null 2>&1; then
        miss "Docker Compose plugin" "$(docker_hint)"
    elif ! _docker_err="$(docker info 2>&1 >/dev/null)"; then
        # A refused socket is the user's group, not the daemon: the
        # usual state right after a Docker install, before a new login.
        case "$_docker_err" in
            *"permission denied"*)
                miss "Docker (this user may not use it)" "Add yourself to the docker group: sudo usermod -aG docker \$USER, then re-run (bower-admin picks the new group up itself)" ;;
            *)
                case "$_os" in
                    Darwin) miss "Docker daemon (not running)" "Launch Docker Desktop and wait for it to finish starting." ;;
                    Linux)  miss "Docker daemon (not running)" "Start it: sudo systemctl start docker" ;;
                    *)      miss "Docker daemon (not running)" "Start the Docker daemon, then re-run." ;;
                esac ;;
        esac
    elif compose_too_old; then
        miss "Docker Compose $(compose_version) (need >= ${COMPOSE_MIN_MAJOR}.${COMPOSE_MIN_MINOR})" "Update Docker Desktop / the Compose plugin: $(docker_hint)"
    else
        pass "Docker + Compose $(compose_version) (daemon running)"
    fi
    if [ -n "$_fixes" ]; then
        if [ -n "${OPENBOWER_VIA_CURL:-}" ]; then
            _rerun="curl -fsSL https://openbower.ai | sh"
        else
            _rerun="sh scripts/bower-admin.sh install"
        fi
        printf '\n%s\n%sThen re-run: %s\n' "${BOLD}Missing prerequisites. Install:${NC}" "$_fixes" "$_rerun" >&2
        return 1
    fi
}

# ---------------------------------------------------------------- lifecycle

compose_up() {
    seed_config
    ensure_network
    compose up -d --wait --wait-timeout "$WAIT_TIMEOUT" "$@"
}

cmd_up() {
    compose_up "$@"
    [ $# -eq 0 ] || return 0
    ok "up: the app at $APP_URL, the api at :8002 (bower-admin logs to tail, bower-admin stop to stop)"
    [ -e "$PROVIDERS" ] || cmd_setup
}

# --renew-anon-volumes: the anonymous .venv volume survives a recreate
# otherwise, so a freshly built image's venv would be masked by the old
# container's and the rebuild would deliver nothing.
cmd_build() {
    compose_up --build --renew-anon-volumes "$@"
}

cmd_stop() {
    info "stopping (a consumer finishes the row it is on first, which can take minutes)"
    compose stop "$@"
}

cmd_down() { compose down "$@"; }
cmd_restart() { compose restart "$@"; }
cmd_status() { compose ps; }
cmd_logs() { compose logs -f "$@"; }

# ---------------------------------------------------------------- version

latest_release_tag() {
    # No jq on a fresh box: pull each tag_name onto its own line and keep
    # the first clean vMAJOR.MINOR.PATCH (the list is newest first, and
    # the anchor skips prereleases).
    command -v curl >/dev/null 2>&1 || return 0
    curl -fsSL "https://api.github.com/repos/$REPO_SLUG/releases?per_page=100" 2>/dev/null \
        | grep -oE '"tag_name"[[:space:]]*:[[:space:]]*"[^"]+"' \
        | sed -E 's/.*"([^"]+)"$/\1/' \
        | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
        | head -n1
}

cmd_version() {
    _current="$(cat version.txt 2>/dev/null || echo '?')"
    info "installed: v$_current ($(git rev-parse --short HEAD 2>/dev/null || echo 'not a git checkout'))"
    _latest="$(latest_release_tag)"
    if [ -n "$_latest" ]; then
        info "newest release: $_latest"
    else
        info "newest release: (could not reach the releases list)"
    fi
}

# ---------------------------------------------------------------- upgrade

cmd_upgrade() {
    command -v git >/dev/null 2>&1 || fail "git is required to upgrade."
    command -v docker >/dev/null 2>&1 || fail "Docker is required to upgrade."
    [ -d .git ] || fail "Not a git checkout: upgrade advances the checkout the installer cloned, so run it there."
    # Tracked edits would make the checkout fail or lose them; untracked
    # files (the .env files, config/) are left alone.
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        fail "You have uncommitted changes to tracked files. Commit or stash them, then re-run."
    fi
    _current="$(cat version.txt 2>/dev/null || echo '?')"
    info "Fetching releases..."
    git fetch --quiet --tags origin || fail "git fetch failed (check your network / remote)."
    # Target: a pinned OPENBOWER_BRANCH, else the newest stable tag among
    # the fetched ones, else main. A portable numeric sort on the three
    # fields, not sort -V, which a bare POSIX sort lacks (and whose
    # failure would be silent inside the pipe).
    _target="${OPENBOWER_BRANCH:-}"
    if [ -z "$_target" ]; then
        _latest="$(git tag --list 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's/^v//' \
            | sort -t. -k1,1n -k2,2n -k3,3n | tail -n1)"
        if [ -n "$_latest" ]; then _target="v$_latest"; else _target="main"; fi
    fi
    # Compare COMMITS, not names: on a branch the name always equals the
    # target, and the fast-forward below would never run.
    if git show-ref --verify --quiet "refs/remotes/origin/$_target"; then
        _target_sha="$(git rev-parse "origin/$_target")"
    else
        _target_sha="$(git rev-parse "refs/tags/$_target^{commit}" 2>/dev/null || echo '')"
    fi
    if [ -n "$_target_sha" ] && [ "$(git rev-parse HEAD)" = "$_target_sha" ]; then
        info "Already up to date (v$_current). Nothing to upgrade."
        return 0
    fi
    step "Upgrading v$_current -> $_target"
    git checkout --quiet "$_target" || fail "git checkout $_target failed."
    if git show-ref --verify --quiet "refs/remotes/origin/$_target"; then
        git merge --ff-only --quiet "origin/$_target" || fail "Could not fast-forward $_target; resolve by hand."
    fi
    # The named database volume survives the rebuild, and migrations run
    # in the setup one-shot on every start, so the data comes along.
    info "Rebuilding the stack (waits for rows in flight)..."
    cmd_build
    _new="$(cat version.txt 2>/dev/null || echo '?')"
    ok "Upgraded to v$_new."
}

# ---------------------------------------------------------------- setup

# A management command in a ONE-OFF container off the built image: it
# reads the config files as they are right now, needs no running
# service, and a file the services would refuse at boot is refused
# here instead, before they see it. --no-sync runs the image's own
# venv as the image's command does (a fresh container's venv would
# otherwise re-sync, noisily, on every call).
run_manage() {
    docker image inspect "$CORE_IMAGE" >/dev/null 2>&1 || fail "the stack has never been built here; run bower-admin up first"
    compose run --rm --no-deps -T --quiet-pull core \
        uv run --frozen --no-sync --package openbower-core python apps/core/manage.py "$@"
}

# The source matrix; exit 0 when a source is ready.
probe_sources() { run_manage providers; }

cmd_providers() { probe_sources; }

# Rewrite a loopback URL to the host gateway: inside a container,
# localhost (and 127.0.0.1, and 0.0.0.0) is the container.
containerize_url() {
    printf '%s' "$1" | sed -E \
        -e 's#//localhost([:/]|$)#//host.docker.internal\1#' \
        -e 's#//127\.0\.0\.1([:/]|$)#//host.docker.internal\1#' \
        -e 's#//0\.0\.0\.0([:/]|$)#//host.docker.internal\1#'
}

# On Linux the host gateway is the Docker bridge address, not loopback,
# so a server on this machine must listen beyond 127.0.0.1 for a
# container to reach it. Docker Desktop (macOS) routes it to the host
# itself, where Ollama's loopback default is enough.
host_bind_hint() {
    [ "$(uname -s)" = Linux ] || return 0
    info "Linux note: the containers reach this machine at its Docker bridge address, so the server"
    info "  must listen on all interfaces, not just 127.0.0.1:"
    info "    Ollama:  ${CYAN}OLLAMA_HOST=0.0.0.0 ollama serve${NC}      vLLM: ${CYAN}--host 0.0.0.0${NC}"
}

# Refuse what a TOML basic string would need escaping for, and any
# line break: the section is written verbatim, one value per line.
toml_safe() {
    case "$1" in
        *'"'* | *\\* | '' | *'
'*) return 1 ;;
    esac
}

source_name_taken() {
    grep -qE "^name = \"$1\"" "$PROVIDERS" 2>/dev/null
}

# Append one source section. $1 spec, $2 name, $3 base_url, $4 api_key
# (may be empty). Keys live inline, so the file is locked owner-only.
append_source() {
    {
        printf '\n[[%s]]\n' "$1"
        printf 'name = "%s"\n' "$2"
        printf 'base_url = "%s"\n' "$3"
        [ -z "$4" ] || printf 'api_key = "%s"\n' "$4"
    } >> "$PROVIDERS"
    lock_file "$PROVIDERS"
    ok "added [[$1]] \"$2\" to $PROVIDERS"
}

# A message from inside a function whose stdout the caller CAPTURES:
# it must reach the person, never the captured value.
say() { printf '%s\n' "  $1" > /dev/tty; }

ask_source_name() {
    while :; do
        _name="$(ask "  Name for this source [$1]: ")"
        _name="${_name:-$1}"
        if ! toml_safe "$_name"; then
            say "a name cannot contain quotes, backslashes, or line breaks"
        elif source_name_taken "$_name"; then
            say "a source named \"$_name\" already exists in $PROVIDERS"
        else
            printf '%s' "$_name"
            return 0
        fi
    done
}

# One round of the menu. Returns 0 when a section was written, 1 on skip.
setup_menu() {
    printf '\n%s\n' "  Add an inference source:" > /dev/tty
    printf '%s\n' "    1) a server you run (any OpenAI-compatible endpoint: Ollama, vLLM, a gateway)" > /dev/tty
    printf '%s\n' "    2) OpenAI (an API key)" > /dev/tty
    printf '%s\n' "    3) Anthropic (an API key)" > /dev/tty
    printf '%s\n' "    4) skip for now (bower-admin setup asks again; $PROVIDERS carries the examples)" > /dev/tty
    _choice=""
    while [ -z "$_choice" ]; do
        _choice="$(ask "  Choice (1-4): ")"
        case "$_choice" in 1 | 2 | 3 | 4) ;; *) _choice="" ;; esac
    done
    case "$_choice" in
        1)
            _url="$(ask "  Base URL [$OLLAMA_URL]: ")"
            _url="${_url:-$OLLAMA_URL}"
            toml_safe "$_url" || { info "a URL cannot contain quotes, backslashes, or line breaks"; return 1; }
            # A bare host is plain http in practice (a box on the LAN);
            # the vendors, which need https, have their own options.
            case "$_url" in
                http://* | https://*) ;;
                *) _url="http://$_url"; info "no scheme given, using $_url" ;;
            esac
            # The default name says what the picker will group the
            # models under: Ollama by its port, "local" for another
            # server on this machine, "custom" for anything else.
            _default_name=custom
            _rewritten="$(containerize_url "$_url")"
            if [ "$_url" = "$OLLAMA_URL" ]; then
                _default_name=ollama
            elif [ "$_rewritten" != "$_url" ]; then
                _default_name=local
            fi
            _url="$_rewritten"
            _key="$(ask_secret "  API key (Enter for none): ")"
            [ -z "$_key" ] || toml_safe "$_key" || { info "a key cannot contain quotes, backslashes, or line breaks"; return 1; }
            _name="$(ask_source_name "$_default_name")"
            append_source openai_compatible "$_name" "$_url" "$_key"
            case "$_url" in *host.docker.internal*) host_bind_hint ;; esac
            ;;
        2)
            _key="$(ask_secret "  OpenAI API key from ${CYAN}https://platform.openai.com/api-keys${NC}: ")"
            toml_safe "$_key" || { info "a key is required and cannot contain quotes or line breaks"; return 1; }
            _name="$(ask_source_name openai)"
            append_source openai_compatible "$_name" "https://api.openai.com/v1" "$_key"
            ;;
        3)
            _key="$(ask_secret "  Anthropic API key from ${CYAN}https://console.anthropic.com/settings/keys${NC}: ")"
            toml_safe "$_key" || { info "a key is required and cannot contain quotes or line breaks"; return 1; }
            _name="$(ask_source_name anthropic)"
            append_source anthropic_compatible "$_name" "https://api.anthropic.com" "$_key"
            ;;
        4) return 1 ;;
    esac
}

restart_core_services() {
    info "restarting the services that read $PROVIDERS (waits for rows in flight)..."
    # shellcheck disable=SC2086  # a space-separated service list, split on purpose
    compose restart $CORE_SERVICES
}

model_hint() {
    info "The stack needs at least one inference source to run an agent. Options:"
    info "  On this machine: install Ollama (https://ollama.com/download), then"
    info "    ${CYAN}ollama pull gemma4:12b && ollama serve${NC}"
    info "  Your own server, or an OpenAI or Anthropic key"
    info "Then: ${CYAN}bower-admin setup${NC}    (it asks which, and writes the section into $PROVIDERS for you)"
    host_bind_hint
}

# Probe through the one-off container and show the result. Sets
# PROBE_STATE: ready; none (a valid file, nothing answers: the command
# said so); refused (the app would not boot on this file: a Python
# error came back instead); or error (the probe itself could not run,
# a docker-level failure). Compose's own chatter is dropped; the
# matrix and any error stay.
probe_and_report() {
    if _out="$(probe_sources 2>&1)"; then
        PROBE_STATE=ready
    elif printf '%s' "$_out" | grep -q 'CommandError:'; then
        PROBE_STATE=none
    elif printf '%s' "$_out" | grep -qE 'Traceback|Error'; then
        PROBE_STATE=refused
    else
        PROBE_STATE=error
    fi
    if [ "$PROBE_STATE" = refused ]; then
        # The traceback's last line carries the reason; the rest is
        # the import chain that led to it.
        _reason="$(printf '%s\n' "$_out" | grep -E 'Error|Configured' | tail -n1 | sed -E 's/^[A-Za-z_.]*(Error|Configured): //')"
        warn "the app refused $PROVIDERS: $_reason"
        return 0
    fi
    printf '%s\n' "$_out" | grep -vE '^\s*$|Container .* (Creat|Start|Running|Waiting|Healthy)|^\[\+\]|^ [⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏✔]' || true
}

# ---- tools

# Set `tool = "vendor"` in the [tools] table of config/tools.toml: the
# existing line is rewritten in place, a missing one is added right
# under the table header, and a file with no table gets one. Atomic
# through a temp file, as every write to a config file here.
set_tool_wiring() {
    _tmp="$TOOLS.tmp.$$"
    if grep -qE "^$1 = " "$TOOLS"; then
        sed -E "s|^$1 = \".*\"|$1 = \"$2\"|" "$TOOLS" > "$_tmp"
    elif grep -qE '^\[tools\]' "$TOOLS"; then
        awk -v line="$1 = \"$2\"" '{ print } /^\[tools\]/ { print line }' "$TOOLS" > "$_tmp"
    else
        cp "$TOOLS" "$_tmp"
        printf '\n[tools]\n%s = "%s"\n' "$1" "$2" >> "$_tmp"
    fi
    lock_file "$_tmp"
    mv "$_tmp" "$TOOLS"
}

# Append a vendor's credential table. The key lives inline, so the
# file is locked owner-only.
add_vendor_key() {
    printf '\n[%s]\napi_key = "%s"\n' "$1" "$2" >> "$TOOLS"
    lock_file "$TOOLS"
}

# The search step. Web search works keyless through DuckDuckGo, but
# the results are weak and finding contacts has no keyless vendor at
# all, so an install is not really set up until Serper has a key.
# Asks once, when Serper is not ready; writes its table and wires web
# search to it; validates through the one-off container, restoring
# the file if the app refuses it. Sets TOOLS_CHANGED.
tools_setup() {
    TOOLS_CHANGED=""
    step "Tools"
    info "checking the search vendors..."
    _matrix="$(run_manage tools 2>&1)" || true
    printf '%s\n' "$_matrix" | grep -E '^[a-z_]+: ' || true
    if ! printf '%s' "$_matrix" | grep -q 'serper (needs setup'; then
        return 0
    fi
    if ! has_tty; then
        info "Web search runs keyless through DuckDuckGo until Serper has a key; finding contacts needs one. Run: bower-admin setup"
        return 0
    fi
    warn "Without a search key, research is thinner: web search falls back to DuckDuckGo, and"
    info "  finding contacts is not available."
    info "  1. Sign up at ${CYAN}https://serper.dev/signup${NC} (free: 2,500 queries to start, no card needed)"
    info "  2. Copy the API key from ${CYAN}https://serper.dev/api-key${NC}"
    while :; do
        _key="$(ask_secret "  Serper API key from ${CYAN}https://serper.dev/api-key${NC} (Enter to skip): ")"
        if [ -z "$_key" ]; then
            info "Skipped: web search stays on DuckDuckGo and finding contacts stays off until a key is added with bower-admin setup"
            return 0
        fi
        toml_safe "$_key" || { say "a key cannot contain quotes, backslashes, or line breaks"; continue; }
        _before="$TOOLS.before.$$"
        cp "$TOOLS" "$_before"
        add_vendor_key serper "$_key"
        set_tool_wiring web_search serper
        info "checking the file..."
        if ! _matrix="$(run_manage tools 2>&1)"; then
            cp "$_before" "$TOOLS"; rm -f "$_before"
            _reason="$(printf '%s\n' "$_matrix" | grep -E 'Error|Configured' | tail -n1 | sed -E 's/^[A-Za-z_.]*(Error|Configured): //')"
            warn "That entry was refused ($_reason), please try again."
            continue
        fi
        # One live query through the app's own client: a 403 is the
        # key and nothing else, so that entry goes; a network failure
        # keeps the key and says the check could not run.
        info "checking the key..."
        _check="$(run_manage tools --probe serper 2>&1)" || true
        case "$_check" in
            *"serper: ok"*)
                rm -f "$_before"
                TOOLS_CHANGED=1
                ok "Serper is wired for web search and finding contacts ($TOOLS)"
                printf '%s\n' "$_matrix" | grep -E '^[a-z_]+: ' || true
                return 0
                ;;
            *"serper: rejected"*)
                cp "$_before" "$TOOLS"; rm -f "$_before"
                warn "Serper rejected that key, please try again."
                ;;
            *)
                rm -f "$_before"
                TOOLS_CHANGED=1
                warn "could not reach serper.dev to check the key; it is saved as given ($(printf '%s\n' "$_check" | tail -n1))"
                return 0
                ;;
        esac
    done
}

# The model step. OpenBower is bring-your-own-model and a model is chosen
# per agent in the builder, so setup only has to leave the install with
# at least one source that ANSWERS. Every probe runs in a one-off
# container against the file as it is, so a bad edit is caught before
# the running services read it; an entry that does not answer is
# dropped again; then the search vendor is asked for; the services
# restart ONCE at the end, when either file changed. The template ships no live source, so a source exists
# only because the person added it AND it answered. Never runs a
# completion; a roster listing is free.
cmd_setup() {
    step "Model setup"
    _changed=""
    if [ ! -e "$PROVIDERS" ]; then
        cp "$PROVIDERS_TEMPLATE" "$PROVIDERS"
        lock_file "$PROVIDERS"
        ok "seeded $PROVIDERS from the template (no source is live until one is added)"
        _changed=1
    fi
    info "probing the configured sources..."
    probe_and_report
    if ! has_tty; then
        case "$PROBE_STATE" in
            ready) ok "an inference source is ready; pick its model in the agent builder" ;;
            refused) warn "$PROVIDERS would be refused at start; fix it, then: bower-admin setup" ;;
            error) warn "could not probe the sources (see above); fix that, then: bower-admin setup" ;;
            *) model_hint ;;
        esac
    else
        while [ "$PROBE_STATE" != ready ]; do
            if [ "$PROBE_STATE" = error ]; then
                warn "could not probe the sources (see above); fix that, then: bower-admin setup"
                break
            fi
            [ "$PROBE_STATE" != refused ] || warn "$PROVIDERS as it stands would be refused at start; fix it by hand, then: bower-admin setup"
            _before="$PROVIDERS.before.$$"
            cp "$PROVIDERS" "$_before"
            if ! setup_menu; then
                rm -f "$_before"
                model_hint
                break
            fi
            info "probing the configured sources..."
            probe_and_report
            case "$PROBE_STATE" in
                ready) _changed=1 ;;
                refused)
                    cp "$_before" "$PROVIDERS"
                    warn "That entry was refused, please try again."
                    PROBE_STATE=none
                    ;;
                error)
                    cp "$_before" "$PROVIDERS"
                    ;;
                none)
                    # A source that does not answer is not a source:
                    # the entry goes, and the file is as it was.
                    cp "$_before" "$PROVIDERS"
                    warn "That source did not answer, please try again."
                    ;;
            esac
            rm -f "$_before"
        done
        [ "$PROBE_STATE" != ready ] || ok "an inference source is ready; pick its model in the agent builder"
    fi
    # The tools matrix boots the same settings, so a file the app
    # refuses has nothing to show here.
    case "$PROBE_STATE" in refused | error) return 0 ;; esac
    tools_setup
    if { [ -n "$_changed" ] || [ -n "$TOOLS_CHANGED" ]; } && core_running; then
        restart_core_services
    fi
}

# ---------------------------------------------------------------- install

# The PATH line, written with $HOME unexpanded when the dir is under it,
# so the rc file reads the way a person would have written it. The single
# quotes are the point: $HOME and $PATH must land in the file unexpanded.
# shellcheck disable=SC2016
path_line() {
    case "$BIN_DIR" in
        "$HOME/"*) printf 'export PATH="$HOME/%s:$PATH"' "${BIN_DIR#"$HOME/"}" ;;
        *)         printf 'export PATH="%s:$PATH"' "$BIN_DIR" ;;
    esac
}

PATH_MARKER="# bower-admin (OpenBower)"

# A path for a message: ~ for the home prefix, the way a person reads it
# (the quoted tilde is the literal we print, not an expansion).
# shellcheck disable=SC2088
tilde() {
    case "$1" in
        "$HOME/"*) printf '~/%s' "${1#"$HOME/"}" ;;
        *)         printf '%s' "$1" ;;
    esac
}

# Append the guarded PATH block to rc file $1, once: the marker makes a
# re-run a no-op, and nothing is ever rewritten or removed. Creates the
# file when absent (a fresh account often has no ~/.zshrc yet).
# Echoes the file on a write, nothing when it was already there.
rc_append() {
    if [ -e "$1" ] && grep -qF "$PATH_MARKER" "$1"; then
        return 0
    fi
    printf '\n%s\n%s\n' "$PATH_MARKER" "$(path_line)" >> "$1"
    tilde "$1"; printf '\n'
}

# The rc files the person's login shell reads, so a new terminal has the
# dir: zsh its .zshrc; bash .bashrc and .profile, plus .bash_profile ONLY
# when it already exists. A bash login shell reads the first of
# .bash_profile, .bash_login, .profile that exists, so creating a
# .bash_profile on a Debian or Ubuntu account (which ships .profile and
# no .bash_profile) would shadow .profile and silently stop .bashrc from
# being sourced. Every other shell gets the printed line instead.
rc_files() {
    case "$(basename "${SHELL:-sh}")" in
        zsh)  printf '%s\n' "${ZDOTDIR:-$HOME}/.zshrc" ;;
        bash)
            printf '%s\n' "$HOME/.bashrc" "$HOME/.profile"
            [ ! -e "$HOME/.bash_profile" ] || printf '%s\n' "$HOME/.bash_profile"
            ;;
        sh | dash | ash) printf '%s\n' "$HOME/.profile" ;;
        *) ;;
    esac
}

# Put the shim's dir on the PATH for new terminals, the way a toolchain
# installer does (no sudo, no system dirs): a guarded line appended to
# the shell rc files. Skipped when the dir is already on the PATH, and
# under OPENBOWER_NO_MODIFY_PATH. Sets PATH_HINT for the summary.
ensure_path() {
    PATH_HINT=""
    case ":$PATH:" in
        *":$BIN_DIR:"*) return 0 ;;
    esac
    if [ -n "${OPENBOWER_NO_MODIFY_PATH:-}" ]; then
        PATH_HINT="$(tilde "$BIN_DIR") is not on your PATH (left alone, OPENBOWER_NO_MODIFY_PATH is set). Add it: $(path_line)"
        warn "$PATH_HINT"
        return 0
    fi
    _written=""
    for _rc in $(rc_files); do
        _done="$(rc_append "$_rc")"
        [ -z "$_done" ] || _written="${_written:+$_written, }$_done"
    done
    _first="$(rc_files | head -n1)"
    [ -z "$_first" ] || _first="$(tilde "$_first")"
    if [ -n "$_written" ]; then
        ok "added $(tilde "$BIN_DIR") to your PATH in $_written"
        PATH_HINT="To use bower-admin in THIS terminal: source $_first    (or open a new terminal)"
    elif [ -n "$_first" ]; then
        PATH_HINT="$(tilde "$BIN_DIR") is on your PATH in $_first but not in this terminal: source $_first    (or open a new terminal)"
    else
        PATH_HINT="$(tilde "$BIN_DIR") is not on your PATH and your shell is not one this install edits. Add it: $(path_line)"
    fi
    warn "$PATH_HINT"
}

cmd_shim() {
    mkdir -p "$BIN_DIR"
    _shim="$BIN_DIR/bower-admin"
    cat > "$_shim" <<SHIM
#!/bin/sh
# Written by OpenBower's install; the command lives in the checkout, so an
# upgrade of the checkout upgrades it.
exec sh "$ROOT/scripts/bower-admin.sh" "\$@"
SHIM
    chmod +x "$_shim"
    ok "bower-admin installed at $(tilde "$_shim")"
    ensure_path
}

cmd_install() {
    cmd_doctor || exit 1
    step "Building and starting the stack (the first build takes a while)"
    cmd_build
    cmd_shim
    [ -e "$PROVIDERS" ] || cmd_setup
    cat <<SUMMARY

${GREEN}Your OpenBower is ready.${NC}

Open the app:   ${CYAN}${APP_URL}${NC}
Sign in there with your OpenBower account (accounts live at the hosted hub; sign up from the login page).
Everything but the login itself runs on this machine.

Operate it with ${CYAN}bower-admin${NC} (the one command this install puts on your PATH; there is no separate CLI yet):
  ${CYAN}bower-admin status${NC}      what is running
  ${CYAN}bower-admin logs${NC}        tail every service (or one: bower-admin logs core)
  ${CYAN}bower-admin stop${NC}        stop in place; ${CYAN}bower-admin up${NC} resumes
  ${CYAN}bower-admin setup${NC}       add an inference source ($PROVIDERS) or the search key ($TOOLS)
  ${CYAN}bower-admin upgrade${NC}     move to the newest release (your data stays)
  ${CYAN}bower-admin help${NC}        everything else

Model and search vendors are yours to bring: $PROVIDERS and $TOOLS (each file explains its shape).
SUMMARY
    [ -z "$PATH_HINT" ] || printf '%s\n\n' "${YELLOW}${PATH_HINT}${NC}"
}

# ---------------------------------------------------------------- help

cmd_help() {
    cat <<HELP
bower-admin: operate a self-hosted OpenBower

  install              first-time setup: prerequisites, build, start, model setup, this command on PATH
  up [service...]      start the stack (seeds the config files when absent; runs setup until it has run)
  build                rebuild the images and start (after an upgrade or a dependency change)
  stop [service...]    stop in place (a consumer finishes its row first); up resumes
  down                 stop and remove the containers (the database volume survives)
  restart [service...] restart in place
  upgrade              advance the checkout to the newest release and rebuild; data preserved
  status               what is running
  logs [service]       tail the logs (all services, or one: core, jobs, fill-consumer, web, ...)
  version              the installed version and the newest release
  doctor               the prerequisites checklist
  setup                add an inference source ($PROVIDERS) and the Serper key ($TOOLS); asks only for what is missing
  shim                 put bower-admin on the PATH for this checkout (install does this): the shim in
                       ~/.local/bin and a guarded PATH line in your shell rc (OPENBOWER_NO_MODIFY_PATH=1 skips the rc)
  providers            the source matrix (what each inference source serves, or why not)
  help                 this

Env: OPENBOWER_BRANCH pins what upgrade moves to (a tag or branch); OPENBOWER_BIN_DIR is where install puts the shim;
     OPENBOWER_NO_MODIFY_PATH=1 leaves your shell rc files alone.
HELP
}

# Single-quote one word for a shell command string (every ' becomes
# '\''), so a re-exec carries arguments exactly.
shell_quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

# Right after Docker's install script adds the user to the docker group,
# the socket still refuses this login (a process's groups are fixed when
# it starts), and the usual advice is to log out and in. Instead, when
# /etc/group already says the user is a member, run this command again
# under that group with sg: same command, same arguments, no new shell
# to exit, no password for one's own group. Once only, by the marker.
adopt_docker_group() {
    [ -z "${OPENBOWER_SG:-}" ] || return 0
    [ "$(uname -s)" = Linux ] || return 0
    command -v docker >/dev/null 2>&1 && command -v sg >/dev/null 2>&1 || return 0
    _err="$(docker info 2>&1 >/dev/null)" && return 0
    case "$_err" in *"permission denied"*) ;; *) return 0 ;; esac
    id -nG 2>/dev/null | grep -qw docker && return 0
    getent group docker 2>/dev/null | cut -d: -f4 | tr ',' '\n' | grep -qx "$(id -un)" || return 0
    info "your docker group membership is new; using it for this run (no need to log out and in)"
    _cmdline="sh $(shell_quote "$0")"
    for _a in "$@"; do _cmdline="$_cmdline $(shell_quote "$_a")"; done
    OPENBOWER_SG=1 exec sg docker -c "$_cmdline"
}

main() {
    adopt_docker_group "$@"
    _cmd="${1:-help}"
    [ $# -eq 0 ] || shift
    case "$_cmd" in
        install)   cmd_install "$@" ;;
        up)        cmd_up "$@" ;;
        build)     cmd_build "$@" ;;
        stop)      cmd_stop "$@" ;;
        down)      cmd_down "$@" ;;
        restart)   cmd_restart "$@" ;;
        upgrade)   cmd_upgrade "$@" ;;
        status)    cmd_status "$@" ;;
        logs)      cmd_logs "$@" ;;
        version)   cmd_version "$@" ;;
        doctor)    cmd_doctor "$@" ;;
        setup)     cmd_setup "$@" ;;
        shim)      cmd_shim "$@" ;;
        providers) cmd_providers "$@" ;;
        help | -h | --help) cmd_help ;;
        *) printf '%s\n\n' "unknown command: $_cmd" >&2; cmd_help >&2; exit 2 ;;
    esac
}

# Sourced for its functions (a test harness) when BOWER_ADMIN_SOURCED
# is set; run as the command otherwise. A sourcing shell's $0 is the
# shell, so ROOT above resolves wrong: a harness cds to the checkout
# first and uses absolute config paths.
[ -n "${BOWER_ADMIN_SOURCED:-}" ] || main "$@"
