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
                       │  LLM: http://nuc3080:8080/v1   ◀── your model
                       └  TTS: EdgeTTS (or a local TTS)
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

Check it in a browser: `http://nuc3080:8003/xiaozhi/ota/` should return a
message saying the OTA interface is running, with the WebSocket address.

All the knobs (LLM host, port, model, voice, ports) live in `.env`. Change them
and re-run `./render-config.sh && docker compose restart`.

Notes for nuc3080:

- Its firewall only allows 8080. Open 8000 and 8003 to the LAN through the
  homelab inventory (`inventory/host_vars/nuc3080.yml`), not a hand-made ufw rule.
- The GPU has little free VRAM with Strata loaded. This compose file doesn't
  pass the GPU through, so speech recognition runs on the CPU and doesn't compete.
- `LLM_THINKING=off` sends `{"thinking": {"type": "disabled"}}`. Strata was
  tested with `chat_template_kwargs.enable_thinking=false` instead, so if
  replies are slow or empty, check the server log to see whether it honours
  this form.

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
