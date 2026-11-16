#!/usr/bin/env sh
# Render data/.config.yaml from config.yaml.template using values in .env.
set -eu
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "No .env found. Run: cp .env.example .env  (then edit it)" >&2
  exit 1
fi

set -a
. ./.env
set +a

mkdir -p data models/SenseVoiceSmall

# Substitute only our own variables (plain sed, no envsubst dependency).
out=$(cat config.yaml.template)
for v in XIAOZHI_HOST XIAOZHI_WS_PORT XIAOZHI_HTTP_PORT LLM_TYPE LLM_URL LLM_MODEL LLM_API_KEY LLM_THINKING TTS_VOICE REPLY_LANGUAGE ASR_LANGUAGE TIMEZONE_OFFSET; do
  eval "val=\${$v:?$v is not set in .env}"
  esc=$(printf '%s' "$val" | sed 's/[|&\\]/\\&/g')
  out=$(printf '%s\n' "$out" | sed "s|\${$v}|$esc|g")
done
printf '%s\n' "$out" > data/.config.yaml
cp agent-prompt.txt data/agent-prompt.txt

echo "Wrote data/.config.yaml"
echo "  LLM:  ${LLM_MODEL} @ ${LLM_URL}"
echo "  OTA URL for the robot: http://${XIAOZHI_HOST}:${XIAOZHI_HTTP_PORT}/xiaozhi/ota/"

if [ ! -s models/SenseVoiceSmall/model.pt ]; then
  echo
  echo "Speech model missing. Download it once with:"
  echo "  curl -L -o models/SenseVoiceSmall/model.pt https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt"
fi
