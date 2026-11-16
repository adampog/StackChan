# Run StackChan's voice assistant on a local LLM (nuc3080)

## Where the LLM call happens today

StackChan's voice assistant is the xiaozhi-esp32 firmware. On boot it calls the
**OTA URL** (`CONFIG_OTA_URL`, default `https://api.tenclass.net/xiaozhi/ota/`),
which answers with a WebSocket/MQTT address. The robot streams audio there, and
the **xiaozhi cloud** does ASR, calls the LLM, and streams TTS back.

The Go server in `server/` never calls an LLM. It only manages agents on
xiaozhi.me through the developer API (`server/internal/xiaozhi/xiaozhi.go`), so
its `llm_model` field can only pick among models the xiaozhi cloud offers.
Pointing it at a LAN address isn't possible.

So the clean way to use your own model is to **replace the xiaozhi cloud with
the self-hosted [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)**
and point it at nuc3080's OpenAI-compatible endpoint:

```
StackChan ──OTA──▶ xiaozhi-server :8003 ──▶ ws://…:8000 (audio)
                       │  ASR:    SenseVoice (local, CPU)
                       │  LLM:    http://nuc3080:8080/v1   ◀── your model
                       │  Vision: the same model (camera questions)
                       └  TTS:    Kokoro (local, CPU, own container)
```

No firmware code changes are needed: the OTA URL is already configurable.

## 1. Check the LLM on nuc3080

nuc3080 (192.168.1.214) runs Strata, a llama.cpp-style server, on port 8080
with an OpenAI-compatible API at `/v1` and one model,
`qwen3.8-flash-next-iq2_xs`. Requests from other machines need the API key:

```sh
curl -H "Authorization: Bearer $STRATA_API_KEY" http://nuc3080:8080/v1/models
```

Checked from the LAN on 2026-11-16: the model answers and makes correct tool
calls, so StackChan's MCP tools (head moves, LEDs, camera) can work. It reasons
before answering by default (about 2.5 s for a short reply, 0.3 s with thinking
off), so `.env` sets `LLM_THINKING=off`.

## 2. Start the xiaozhi server (on nuc3080, or any always-on LAN box)

```sh
cd deploy/local-llm
cp .env.example .env        # set LLM_API_KEY (and host/model if they change)
./render-config.sh          # writes data/.config.yaml and prints the OTA URL
curl -L -o models/SenseVoiceSmall/model.pt \
  https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt
docker compose up -d
docker compose logs -f      # look for the OTA and WebSocket addresses
```

Check it in a browser: `http://192.168.1.214:8003/xiaozhi/ota/` should return a
message saying the OTA interface is running, with the WebSocket address.

All the knobs (LLM host, port, model, voice, ports) live in `.env`. Change them
and re-run `./render-config.sh && docker compose restart`.

Notes for nuc3080:

- Its firewall only allows 8080. Open 8000 and 8003 to the LAN through the
  homelab inventory (`inventory/host_vars/nuc3080.yml`), not a hand-made ufw rule.
- The GPU has little free VRAM with Strata loaded. This compose file doesn't
  pass the GPU through, so speech recognition runs on the CPU and doesn't compete.
- Strata ignores the stock provider's thinking-off parameter, so this setup
  mounts a small provider (`providers/llamacpp`, `LLM_TYPE=llamacpp`) that
  sends `chat_template_kwargs.enable_thinking=false` instead.
- Replies are in `REPLY_LANGUAGE` (English). The stock server defaults to
  Chinese and a long Chinese-flavoured system prompt; `agent-prompt.txt` is a
  short English replacement, and the weather/news/search plugins are off so
  the first prompt stays small.
- Containers reach Strata through the Docker bridge, so the Strata firewall
  rule must also allow `172.16.0.0/12`.

## 3. Point the robot at it

Pick one:

- **No reflash:** the firmware reads an `ota_url` override from NVS (`wifi`
  namespace) before falling back to `CONFIG_OTA_URL`. If your provisioning flow
  exposes it (the xiaozhi Wi-Fi setup page has it under Advanced), set it to
  `http://192.168.1.214:8003/xiaozhi/ota/`.
- **Rebuild:** `cp firmware/sdkconfig.defaults.local.example firmware/sdkconfig.defaults.local`,
  edit the host if needed, then `idf.py fullclean && idf.py build flash`.

If the robot can't resolve `nuc3080`, use the machine's IP in both `.env`
(`XIAOZHI_HOST`) and the OTA URL. A DHCP reservation keeps it stable.

## Local or cloud, per piece

Everything runs on nuc3080 by default. Each piece can be moved to a cloud
service and back without touching the robot:

```sh
./switch.sh status
./switch.sh llm cloud        # asr | llm | tts | vision | all, then local | cloud
./switch.sh all local
```

It updates `USE_*` in `.env`, re-renders the config and restarts the voice
server (set `DOCKER="sudo docker"` if needed). The robot reconnects by itself
the next time you talk to it.

| Piece  | local                         | cloud                                         |
|--------|-------------------------------|-----------------------------------------------|
| ASR    | SenseVoiceSmall on the CPU    | OpenAI-compatible transcription (`CLOUD_ASR_*`) |
| LLM    | Strata on nuc3080             | OpenAI-compatible chat API (`CLOUD_LLM_*`)    |
| Vision | Strata on nuc3080             | OpenAI-compatible vision model (`CLOUD_VISION_*`) |
| TTS    | Kokoro (`kokoro-tts` container) | Microsoft Edge TTS (free, no key)           |

Cloud keys go in `.env` only. `switch.sh` refuses to switch a piece to cloud
while its key is still `changeme`.

Even fully local, two things still use the internet: pulling the Docker
images and the speech model once at install time, and whatever the robot's
firmware does by itself (time sync, for example).

## What changes for you

- The device no longer binds to xiaozhi.me, so agent/voice settings made in the
  StackChan app (which go through the Go server to xiaozhi.me) don't apply to
  it. The prompt, voice and model now come from `.env` and `config.yaml.template`.
- The StackChan backend (`CONFIG_STACKCHAN_SERVER_URL`: avatar, apps, camera
  WebSocket) is separate and unaffected.
