#!/usr/bin/env sh
# Switch a piece of the voice pipeline between local and cloud, on the fly.
#   ./switch.sh status
#   ./switch.sh <asr|llm|tts|vision|all> <local|cloud>
# Updates .env, re-renders the config and restarts the voice server (the robot
# reconnects by itself; no reflash). Set DOCKER="sudo docker" if needed.
set -eu
cd "$(dirname "$0")"
DOCKER=${DOCKER:-docker}

[ -f .env ] || { echo "No .env found. Run: cp .env.example .env" >&2; exit 1; }

get() { # current value of a setting: .env wins over .env.example
  v=$(grep -E "^$1=" .env | tail -n 1 | cut -d= -f2-)
  [ -n "$v" ] || v=$(grep -E "^$1=" .env.example | tail -n 1 | cut -d= -f2-)
  echo "$v"
}

status() {
  echo "ASR:    $(get USE_ASR)"
  echo "LLM:    $(get USE_LLM)"
  echo "TTS:    $(get USE_TTS)"
  echo "Vision: $(get USE_VISION)"
}

set_var() { # set_var NAME VALUE in .env (replace or append)
  if grep -qE "^$1=" .env; then
    tmp=$(mktemp)
    sed "s|^$1=.*|$1=$2|" .env > "$tmp" && cat "$tmp" > .env && rm -f "$tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

need_key() { # refuse cloud when its key is still the placeholder
  k=$(get "$1")
  if [ -z "$k" ] || [ "$k" = changeme ]; then
    echo "Set $1 (and its URL/model) in .env before switching $2 to cloud." >&2
    exit 1
  fi
}

piece=${1:-status}
[ "$piece" = status ] && { status; exit 0; }
mode=${2:-}
case "$mode" in local|cloud) ;; *) echo "usage: $0 <asr|llm|tts|vision|all> <local|cloud>" >&2; exit 1 ;; esac

case "$piece" in asr|llm|tts|vision) pieces=$piece ;; all) pieces="asr llm tts vision" ;;
  *) echo "unknown piece '$piece' (asr, llm, tts, vision, all)" >&2; exit 1 ;; esac

for p in $pieces; do
  case "$p" in
    asr)    [ "$mode" = cloud ] && need_key CLOUD_ASR_API_KEY asr;       set_var USE_ASR "$mode" ;;
    llm)    [ "$mode" = cloud ] && need_key CLOUD_LLM_API_KEY llm;       set_var USE_LLM "$mode" ;;
    tts)    set_var USE_TTS "$mode" ;;   # cloud TTS is Edge TTS: no key
    vision) [ "$mode" = cloud ] && need_key CLOUD_VISION_API_KEY vision; set_var USE_VISION "$mode" ;;
  esac
done

./render-config.sh >/dev/null
$DOCKER compose restart xiaozhi-server
status
