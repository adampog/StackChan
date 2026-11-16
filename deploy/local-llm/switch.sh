#!/usr/bin/env sh
# Switch pieces of the voice pipeline, on the fly. Nothing is flashed and
# nothing changes on the robot.
#
#   ./switch.sh status
#   ./switch.sh llm <local|claude|openrouter|openrouter-free|huggingface|deepseek|custom>
#   ./switch.sh claude                 short for: llm claude
#   ./switch.sh vision <local|claude|custom>
#   ./switch.sh <asr|tts> <local|cloud>
#   ./switch.sh all local              everything back on nuc3080
#
# llm and vision take effect on the next sentence: the voice server reads the
# choice from a small file (data/llm-preset, data/vision-preset) per request.
# asr and tts restart the voice server (about 6 s; the robot reconnects by
# itself). Set DOCKER="sudo docker" if needed.
set -eu
cd "$(dirname "$0")"
DOCKER=${DOCKER:-docker}
LLM_PRESETS="local claude openrouter openrouter-free huggingface deepseek custom"
VISION_PRESETS="local claude custom"

[ -f .env ] || { echo "No .env found. Run: cp .env.example .env" >&2; exit 1; }

get() { # current value of a setting: .env wins over .env.example
  v=$(grep -E "^$1=" .env | tail -n 1 | cut -d= -f2-)
  [ -n "$v" ] || v=$(grep -E "^$1=" .env.example | tail -n 1 | cut -d= -f2-)
  echo "$v"
}

set_var() { # set_var NAME VALUE in .env (replace or append)
  if grep -qE "^$1=" .env; then
    tmp=$(mktemp)
    sed "s|^$1=.*|$1=$2|" .env > "$tmp" && cat "$tmp" > .env && rm -f "$tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

in_list() { # in_list WORD "a b c"
  for w in $2; do [ "$w" = "$1" ] && return 0; done
  return 1
}

preset() { # normalise a preset name; "cloud" is the old name for custom
  case "$1" in cloud) echo custom ;; *) echo "$1" ;; esac
}

active() { # active <llm|vision>: the state file, else the USE_ default
  f=data/$1-preset
  if [ "$1" = llm ]; then list=$LLM_PRESETS; def=$(preset "$(get USE_LLM)")
  else list=$VISION_PRESETS; def=$(preset "$(get USE_VISION)"); fi
  cur=$( { [ -f "$f" ] && head -n 1 "$f" | tr -d '[:space:]'; } || true)
  if [ -n "$cur" ] && in_list "$cur" "$list"; then echo "$cur"; else echo "$def"; fi
}

key_var() { # key_var <llm|vision> PRESET: the .env setting holding its key
  case "$2" in
    local) echo LLM_API_KEY ;;
    claude) echo ANTHROPIC_API_KEY ;;
    openrouter|openrouter-free) echo OPENROUTER_API_KEY ;;
    huggingface) echo HF_TOKEN ;;
    deepseek) echo DEEPSEEK_API_KEY ;;
    custom) [ "$1" = llm ] && echo CLOUD_LLM_API_KEY || echo CLOUD_VISION_API_KEY ;;
  esac
}

need_key() { # refuse a backend whose key is still the placeholder
  k=$(get "$1")
  if [ -z "$k" ] || [ "$k" = changeme ]; then
    echo "Set $1 in .env first (then run this again)." >&2
    exit 1
  fi
}

status() {
  echo "ASR:    $(get USE_ASR)"
  echo "LLM:    $(active llm)"
  echo "TTS:    $(get USE_TTS)"
  echo "Vision: $(active vision)"
}

RESTART=no
config_stale() { # keys or settings in .env changed since the last render
  [ ! -f data/.config.yaml ] || [ .env -nt data/.config.yaml ] \
    || ! grep -q 'type: router' data/.config.yaml
}

pick() { # pick <llm|vision> PRESET: write the state file the server reads
  mkdir -p data
  tmp=data/.$1-preset.$$
  printf '%s\n' "$2" > "$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "data/$1-preset"
}

switch_llm() {
  p=$(preset "$1")
  in_list "$p" "$LLM_PRESETS" || { echo "unknown LLM '$1' (one of: $LLM_PRESETS)" >&2; exit 1; }
  [ "$p" = local ] || need_key "$(key_var llm "$p")"
  pick llm "$p"
  case "$p" in
    openrouter-free|huggingface|openrouter|custom)
      echo "Note: the robot moves its head and LEDs through tool calls. If the"
      echo "      chosen model can't call tools it will still talk, but not move." ;;
  esac
}

switch_vision() {
  p=$(preset "$1")
  in_list "$p" "$VISION_PRESETS" || { echo "unknown vision backend '$1' (one of: $VISION_PRESETS)" >&2; exit 1; }
  [ "$p" = local ] || need_key "$(key_var vision "$p")"
  pick vision "$p"
}

switch_asr() {
  case "$1" in local|cloud) ;; *) echo "asr is local or cloud" >&2; exit 1 ;; esac
  [ "$1" = cloud ] && need_key CLOUD_ASR_API_KEY
  [ "$(get USE_ASR)" = "$1" ] || { set_var USE_ASR "$1"; RESTART=yes; }
}

switch_tts() { # cloud TTS is Edge TTS: no key
  case "$1" in local|cloud) ;; *) echo "tts is local or cloud" >&2; exit 1 ;; esac
  [ "$(get USE_TTS)" = "$1" ] || { set_var USE_TTS "$1"; RESTART=yes; }
}

piece=${1:-status}
[ "$piece" = status ] && { status; exit 0; }
if in_list "$piece" "$LLM_PRESETS"; then set -- llm "$piece"; piece=llm; fi
choice=${2:-}
[ -n "$choice" ] || { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 1; }

config_stale && RESTART=yes
case "$piece" in
  llm)    switch_llm "$choice" ;;
  vision) switch_vision "$choice" ;;
  asr)    switch_asr "$choice" ;;
  tts)    switch_tts "$choice" ;;
  all)
    case "$choice" in
      local) switch_asr local; switch_tts local; switch_llm local; switch_vision local ;;
      cloud) switch_asr cloud; switch_tts cloud; switch_llm custom; switch_vision custom ;;
      *) echo "all is local or cloud" >&2; exit 1 ;;
    esac ;;
  *) echo "unknown piece '$piece' (llm, vision, asr, tts, all, status)" >&2; exit 1 ;;
esac

if [ "$RESTART" = yes ]; then
  ./render-config.sh >/dev/null
  $DOCKER compose restart xiaozhi-server
fi
status
