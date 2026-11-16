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
                       │  ASR: SenseVoice (local)
                       │  LLM: http://nuc3080:11434/v1  ◀── your model
                       └  TTS: EdgeTTS (or a local TTS)
```

No firmware code changes are needed: the OTA URL is already configurable.

## 1. Check the LLM on nuc3080

From another machine on the LAN:

```sh
curl http://nuc3080:11434/v1/models          # Ollama; adjust port for llama.cpp / LM Studio / vLLM
```

If it only answers on localhost, make it listen on the LAN (Ollama: set
`OLLAMA_HOST=0.0.0.0` and restart). Pick a model that supports tool calling
(for example `qwen2.5:7b` or `llama3.1:8b`) so StackChan's MCP tools (head moves,
LEDs, camera) keep working.

## 2. Start the xiaozhi server (on nuc3080, or any always-on LAN box)

```sh
cd deploy/local-llm
cp .env.example .env        # set LLM_URL, LLM_MODEL, XIAOZHI_HOST
./render-config.sh          # writes data/.config.yaml and prints the OTA URL
curl -L -o models/SenseVoiceSmall/model.pt \
  https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt
docker compose up -d
docker compose logs -f      # look for the OTA and WebSocket addresses
```

Check it in a browser: `http://nuc3080:8003/xiaozhi/ota/` should return a
message saying the OTA interface is running, with the WebSocket address.

All the knobs (LLM host, port, model, voice, ports) live in `.env`. Change them
and re-run `./render-config.sh && docker compose restart`.

## 3. Point the robot at it

Pick one:

- **No reflash:** the firmware reads an `ota_url` override from NVS (`wifi`
  namespace) before falling back to `CONFIG_OTA_URL`. If your provisioning flow
  exposes it (the xiaozhi Wi-Fi setup page has it under Advanced), set it to
  `http://nuc3080:8003/xiaozhi/ota/`.
- **Rebuild:** `cp firmware/sdkconfig.defaults.local.example firmware/sdkconfig.defaults.local`,
  edit the host if needed, then `idf.py fullclean && idf.py build flash`.

If the robot can't resolve `nuc3080`, use the machine's IP in both `.env`
(`XIAOZHI_HOST`) and the OTA URL. A DHCP reservation keeps it stable.

## What changes for you

- The device no longer binds to xiaozhi.me, so agent/voice settings made in the
  StackChan app (which go through the Go server to xiaozhi.me) don't apply to
  it. The prompt, voice and model now come from `.env` and `config.yaml.template`.
- The StackChan backend (`CONFIG_STACKCHAN_SERVER_URL`: avatar, apps, camera
  WebSocket) is separate and unaffected.
- EdgeTTS still calls Microsoft's free TTS service over the internet. For a
  fully offline setup, switch `TTS` to a local engine supported by
  xiaozhi-esp32-server (for example FishSpeech or an OpenAI-compatible TTS on
  the NUC).
