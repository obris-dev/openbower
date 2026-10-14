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
# path is bash-free. This file is the COMMAND: it resolves the checkout,
# loads scripts/lib/bower-admin.sh (every constant and function, which
# runs nothing on load, so the function tests source it too), and
# dispatches.
#
# Env:
#   OPENBOWER_BRANCH   what a bare `upgrade` moves to (a tag or branch, in
#                      either direction, as if you had typed it); unset,
#                      the newest release, forward only. The installer
#                      reads it too, to choose what to clone.
#   OPENBOWER_BIN_DIR  where install writes the bower-admin shim (default
#                      ~/.local/bin)
#   OPENBOWER_NO_MODIFY_PATH=1
#                      never edit a shell rc file; install prints the
#                      PATH line to add by hand instead
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/lib/bower-admin.sh
. "$ROOT/scripts/lib/bower-admin.sh"

main() {
    if has_tty; then
        TTY_SAVED="$(stty -g < /dev/tty 2>/dev/null || true)"
        trap on_signal INT TERM HUP
    fi
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
        # Internal, absent from help: the new release's half of upgrade,
        # exec'd by the old one. A contract every release must keep, with
        # OPENBOWER_UPGRADED_FROM as the one value crossing the exec.
        post-upgrade) cmd_post_upgrade "$@" ;;
        status)    cmd_status "$@" ;;
        logs)      cmd_logs "$@" ;;
        version)   cmd_version "$@" ;;
        doctor)    cmd_doctor "$@" ;;
        setup)     cmd_setup "$@" ;;
        reload)    cmd_reload "$@" ;;
        shim)      cmd_shim "$@" ;;
        providers) cmd_providers "$@" ;;
        tools)     cmd_tools "$@" ;;
        dbshell)   cmd_dbshell "$@" ;;
        help | -h | --help) cmd_help ;;
        *) printf '%s\n\n' "unknown command: $_cmd" >&2; cmd_help >&2; exit 2 ;;
    esac
}

main "$@"
