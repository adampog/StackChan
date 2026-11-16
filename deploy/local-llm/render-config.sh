#!/usr/bin/env sh
# Render data/.config.yaml from config.yaml.template using values in .env.
set -eu
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "No .env found. Run: cp .env.example .env  (then edit it)" >&2
  exit 1
fi

# .env.example supplies defaults for settings added after .env was created.
set -a
. ./.env.example
. ./.env
set +a

pick() { # pick <local|cloud> <local module> <cloud module> <setting name>
  case "$1" in
    local) echo "$2" ;;
    cloud) echo "$3" ;;
    *) echo "$4 must be local or cloud, not '$1'" >&2; exit 1 ;;
  esac
}
SEL_ASR=$(pick "$USE_ASR" FunASR CloudASR USE_ASR)
SEL_LLM=$(pick "$USE_LLM" NucLLM CloudLLM USE_LLM)
SEL_TTS=$(pick "$USE_TTS" LocalTTS EdgeTTS USE_TTS)
SEL_VLLM=$(pick "$USE_VISION" NucVLLM CloudVLLM USE_VISION)

mkdir -p data models/SenseVoiceSmall

# Substitute only our own variables (plain sed, no envsubst dependency).
out=$(cat config.yaml.template)
for v in XIAOZHI_HOST XIAOZHI_WS_PORT XIAOZHI_HTTP_PORT \
         SEL_ASR SEL_LLM SEL_TTS SEL_VLLM \
         LLM_TYPE LLM_URL LLM_MODEL LLM_API_KEY LLM_THINKING VISION_URL VISION_MODEL \
         CLOUD_LLM_URL CLOUD_LLM_MODEL CLOUD_LLM_API_KEY \
         CLOUD_VISION_URL CLOUD_VISION_MODEL CLOUD_VISION_API_KEY \
         CLOUD_ASR_URL CLOUD_ASR_MODEL CLOUD_ASR_API_KEY \
         LOCAL_TTS_VOICE TTS_VOICE REPLY_LANGUAGE ASR_LANGUAGE TIMEZONE_OFFSET; do
  eval "val=\${$v:?$v is not set in .env}"
  esc=$(printf '%s' "$val" | sed 's/[|&\\]/\\&/g')
  out=$(printf '%s\n' "$out" | sed "s|\${$v}|$esc|g")
done
umask 077   # the rendered config holds API keys
printf '%s\n' "$out" > data/.config.yaml
cp agent-prompt.txt data/agent-prompt.txt

echo "Wrote data/.config.yaml"
echo "  ASR:    $USE_ASR ($SEL_ASR)"
echo "  LLM:    $USE_LLM ($SEL_LLM)"
echo "  TTS:    $USE_TTS ($SEL_TTS)"
echo "  Vision: $USE_VISION ($SEL_VLLM)"
echo "  OTA URL for the robot: http://${XIAOZHI_HOST}:${XIAOZHI_HTTP_PORT}/xiaozhi/ota/"

if [ ! -s models/SenseVoiceSmall/model.pt ]; then
  echo
  echo "Speech model missing. Download it once with:"
  echo "  curl -L -o models/SenseVoiceSmall/model.pt https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt"
fi
