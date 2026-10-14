#!/bin/sh
#
# The operator command's pure helpers, each against known inputs, in a
# throwaway HOME and config directory: the file writers (parsed back
# as TOML), the URL rewrite, the quoting, the rc append, the name rule,
# the release-tag sort. The prompts need a terminal and stay out; the
# VM run covers them. Run: sh scripts/tests/bower-admin-functions.sh
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091  # the file under test, linted on its own
OPENBOWER_ROOT="$ROOT" BOWER_ADMIN_SOURCED=1 . "$ROOT/scripts/bower-admin.sh"
# The script turns -e on; an assertion that fails must report, not abort.
set +e

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
HOME="$T/home"; mkdir -p "$HOME"
PROVIDERS="$T/providers.toml"; TOOLS="$T/tools.toml"
# Read by the sourced ensure_path, so the rc block names a dir under
# the throwaway HOME.
# shellcheck disable=SC2034
BIN_DIR="$HOME/.local/bin"
fails=0
expect() {
    if [ "$2" = "$3" ]; then printf '  + %s\n' "$1"; else printf '  - %s: got [%s] want [%s]\n' "$1" "$2" "$3"; fails=$((fails + 1)); fi
}
# A TOML file's fact, read by Python's own parser: $1 file, $2 expression over d.
toml() { python3 -c "import tomllib,sys; d=tomllib.loads(open(sys.argv[1]).read()); print($2)" "$1" 2>&1 | tail -n1; }

echo "containerize_url"
expect "localhost rewritten"      "$(containerize_url http://localhost:11434/v1)"   "http://host.docker.internal:11434/v1"
expect "127.0.0.1 rewritten"      "$(containerize_url http://127.0.0.1:8000/v1)"    "http://host.docker.internal:8000/v1"
expect "0.0.0.0 rewritten"        "$(containerize_url http://0.0.0.0:8000/v1)"      "http://host.docker.internal:8000/v1"
expect "LAN address kept"         "$(containerize_url http://192.168.1.20:8000/v1)" "http://192.168.1.20:8000/v1"
expect "hostname kept"            "$(containerize_url https://gpu.example.io/v1)"   "https://gpu.example.io/v1"
expect "mylocalhost.io kept"      "$(containerize_url http://mylocalhost.io/v1)"    "http://mylocalhost.io/v1"

echo "shell_quote"
expect "plain word"  "$(shell_quote plain)"       "'plain'"
expect "with spaces" "$(shell_quote 'two words')" "'two words'"
eval "set -- $(shell_quote "it's") $(shell_quote 'two words')"
expect "round trip" "$1|$2" "it's|two words"

echo "toml_safe"
toml_safe ok;            expect "plain accepted"      "$?" 0
toml_safe 'has"quote';   expect "quote refused"       "$?" 1
toml_safe 'back\slash';  expect "backslash refused"   "$?" 1
toml_safe '';            expect "empty refused"       "$?" 1
toml_safe "$(printf 'a\nb')"; expect "line break refused" "$?" 1

echo "rc_files + rc_append"
touch "$HOME/.profile" "$HOME/.bashrc"
SHELL=/bin/bash ensure_path >/dev/null
[ -e "$HOME/.bash_profile" ] && created=yes || created=no
expect "debian-shaped bash: no .bash_profile created" "$created" no
expect "block in .bashrc once"  "$(grep -c "$PATH_MARKER" "$HOME/.bashrc")"  1
SHELL=/bin/bash ensure_path >/dev/null
expect "re-run adds no second block" "$(grep -c "$PATH_MARKER" "$HOME/.bashrc")" 1
touch "$HOME/.bash_profile"; SHELL=/bin/bash ensure_path >/dev/null
expect "existing .bash_profile gets the block" "$(grep -c "$PATH_MARKER" "$HOME/.bash_profile")" 1
SHELL=/bin/zsh ensure_path >/dev/null
expect "zsh: .zshrc only" "$(grep -c "$PATH_MARKER" "$HOME/.zshrc")" 1
# The literal line, with $HOME and $PATH unexpanded, is the point.
# shellcheck disable=SC2016
expect "the line as written" "$(tail -n1 "$HOME/.zshrc")" 'export PATH="$HOME/.local/bin:$PATH"'

echo "set_tool_wiring + add_vendor_key"
cp "$ROOT/config/templates/tools.example.toml" "$TOOLS"
add_vendor_key serper K1; set_tool_wiring web_search serper
expect "template: wired + keyed"  "$(toml "$TOOLS" "d['tools']['web_search'], d['serper']['api_key']")" "serper K1"
add_vendor_key serper K2
expect "rewritten in place, one table" "$(grep -c '^\[serper\]' "$TOOLS")|$(toml "$TOOLS" "d['serper']['api_key']")" "1|K2"
printf '[tools]\nfind_contacts = "serper"\n' > "$TOOLS"; set_tool_wiring web_search serper
expect "table without the line: added under it" "$(toml "$TOOLS" "d['tools']['web_search']")" "serper"
printf '# empty\n' > "$TOOLS"; set_tool_wiring web_search serper; add_vendor_key serper k
expect "no table at all: both created" "$(toml "$TOOLS" "d['tools']['web_search'], d['serper']['api_key']")" "serper k"
printf '[serper]\napi_key = ""\n\n[tools]\nweb_search = "duckduckgo"\n' > "$TOOLS"; add_vendor_key serper K3
expect "blank uncommented table: key filled, tables intact" "$(toml "$TOOLS" "d['serper']['api_key'], d['tools']['web_search']")" "K3 duckduckgo"

echo "append_source"
cp "$ROOT/config/templates/providers.example.toml" "$PROVIDERS"
append_source openai_compatible ollama http://host.docker.internal:11434/v1 "" 1 >/dev/null
append_source anthropic_compatible anthropic https://api.anthropic.com sk-ant "" >/dev/null
expect "two sections parse" "$(toml "$PROVIDERS" "sorted(d), d['openai_compatible'][0]['concurrency'], d['anthropic_compatible'][0]['api_key']")" "['anthropic_compatible', 'openai_compatible'] 1 sk-ant"
source_name_taken ollama;  expect "name taken"              "$?" 0
source_name_taken Ollama;  expect "name taken without case" "$?" 0
source_name_taken vllm;    expect "name free"               "$?" 1

echo "newest_release_tag"
R="$T/repo"; git init -q "$R" && git -C "$R" -c user.name=t -c user.email=t@example.io commit -q --allow-empty -m one
for tag in v0.0.9 v0.0.10 v0.1.0-rc1 cli-v9.9.9; do git -C "$R" tag "$tag"; done
expect "numeric sort, prerelease and foreign tags skipped" "$(cd "$R" && newest_release_tag)" "v0.0.10"

echo "lock_file"
f="$T/secret"; : > "$f"; lock_file "$f"
case "$(uname -s)" in Darwin) want=600 ;; *) [ "$(id -u)" = 1000 ] && want=600 || want=644 ;; esac
case "$(uname -s)" in Darwin) mode="$(stat -f %Lp "$f")" ;; *) mode="$(stat -c %a "$f")" ;; esac
expect "mode for this host and uid" "$mode" "$want"

if [ "$fails" -gt 0 ]; then printf '\n%s assertion(s) failed\n' "$fails"; exit 1; fi
printf '\nall assertions passed\n'
