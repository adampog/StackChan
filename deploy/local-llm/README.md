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
docker compose up -d --build   # builds the image once (adds the Anthropic SDK)
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

## Pick the brain, on the fly

Everything runs on nuc3080 by default. The LLM (and vision) can be pointed at
another backend at any time without touching the robot:

```sh
./switch.sh status
./switch.sh llm claude       # or just: ./switch.sh claude
./switch.sh llm local
./switch.sh vision claude
```

From another machine: `ssh 192.168.1.214 'cd <repo>/deploy/local-llm && ./switch.sh claude'`.
Or say it to the robot: "switch to Claude", "switch to local", "which brain
are you using?".

| `llm` preset      | What it is                                   | Key in `.env`        | Model setting           |
|-------------------|----------------------------------------------|----------------------|-------------------------|
| `local`           | Strata on nuc3080 (free, private)            | `LLM_API_KEY`        | `LLM_MODEL`             |
| `claude`          | Claude, through the official Anthropic SDK   | `ANTHROPIC_API_KEY`  | `CLAUDE_MODEL`, `CLAUDE_EFFORT` |
| `openrouter`      | Any model on OpenRouter                      | `OPENROUTER_API_KEY` | `OPENROUTER_MODEL`      |
| `openrouter-free` | OpenRouter's free models (50 requests a day; 1,000 with $10 of credit) | `OPENROUTER_API_KEY` | `OPENROUTER_FREE_MODEL` |
| `huggingface`     | Hugging Face Inference Providers             | `HF_TOKEN`           | `HF_MODEL`              |
| `deepseek`        | DeepSeek's own API                           | `DEEPSEEK_API_KEY`   | `DEEPSEEK_MODEL`        |
| `custom`          | Any other OpenAI-compatible API              | `CLOUD_LLM_API_KEY`  | `CLOUD_LLM_URL`, `CLOUD_LLM_MODEL` |

Vision presets: `local` (Strata), `claude`, `custom` (`CLOUD_VISION_*`).

How it works:

- The voice server loads one "switchboard" provider (`providers/router`) that
  holds every preset. The active name is a one-line file, `data/llm-preset`
  (`data/vision-preset` for vision), read on every request. So a switch takes
  effect on the next sentence, with no restart and no dropped conversation.
- All of it runs on the voice server. Nothing is added to the robot's
  firmware, memory or tool list.
- If the active brain fails before it has said anything (no internet, wrong
  key), that turn is answered by `local` and the robot says so once.
- A preset can't be chosen while its key is still `changeme`. After editing
  keys or models in `.env`, run any `./switch.sh` command: it re-renders the
  config and restarts the voice server once (about 6 s).
- The robot moves its head and LEDs through tool calls. Claude, DeepSeek and
  the local model support them; on OpenRouter and Hugging Face it depends on
  the model, and a model without tool calling will talk but not move.
- The Claude preset defaults to `claude-opus-5-5` at low effort. Set
  `CLAUDE_MODEL=claude-haiku-5-5` for the fastest, cheapest replies. On Opus,
  Sonnet 5.5 and Fable models a request the model declines is retried
  server-side on Anthropic's recommended fallback model.
- Set `VOICE_SWITCH=false` to turn spoken switching off.

## Speech in and out

Speech recognition and speech output are switched per piece, with a short
restart of the voice server (the robot reconnects the next time you talk):

```sh
./switch.sh tts cloud        # asr | tts, then local | cloud
./switch.sh all local        # everything back on nuc3080
```

| Piece | local                           | cloud                                           |
|-------|---------------------------------|-------------------------------------------------|
| ASR   | SenseVoiceSmall on the CPU      | OpenAI-compatible transcription (`CLOUD_ASR_*`) |
| TTS   | Kokoro (`kokoro-tts` container) | Microsoft Edge TTS (free, no key)               |

Set `DOCKER="sudo docker"` if docker needs sudo.

With everything on local, the voice loop makes no internet connections. What
still does: pulling the Docker images and the speech model at install time,
and the robot's own time sync (NTP) and DNS lookups.

## What changes for you

- The device no longer binds to xiaozhi.me, so agent/voice settings made in the
  StackChan app (which go through the Go server to xiaozhi.me) don't apply to
  it. The prompt, voice and model now come from `.env` and `config.yaml.template`.
- The StackChan backend (`CONFIG_STACKCHAN_SERVER_URL`: avatar, apps, camera
  WebSocket) is separate and unaffected.
