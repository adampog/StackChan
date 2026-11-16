"""Claude vision provider for xiaozhi-esp32-server (official Anthropic SDK).

Mounted into the container as core/providers/vllm/claude.py and selected with
"type: claude". Answers one question about one photo from the robot's camera.
"""

import anthropic

from config.logger import setup_logging
from core.providers.vllm.base import VLLMProviderBase

TAG = __name__
logger = setup_logging()

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")


def media_type(base64_image):
    """Image type from the first base64 characters (the camera sends JPEG)."""
    head = base64_image[:12]
    if head.startswith("iVBOR"):
        return "image/png"
    if head.startswith("R0lG"):
        return "image/gif"
    if head.startswith("UklG"):
        return "image/webp"
    return "image/jpeg"


class VLLMProvider(VLLMProviderBase):
    def __init__(self, config):
        self.model_name = config.get("model_name") or "claude-opus-5-5"
        self.effort = str(config.get("effort") or "").strip().lower()
        self.language = config.get("language") or "English"
        try:
            self.max_tokens = int(config.get("max_tokens") or 2048)
        except (TypeError, ValueError):
            self.max_tokens = 2048
        mode = str(config.get("fallbacks") or "auto").strip().lower()
        if mode in ("on", "true", "yes", "default"):
            self.use_fallbacks = True
        elif mode in ("off", "false", "no", "none"):
            self.use_fallbacks = False
        else:
            self.use_fallbacks = self.model_name.startswith(FALLBACK_MODEL_PREFIXES)
        self.client = anthropic.Anthropic(
            api_key=config.get("api_key"), timeout=60.0, max_retries=1
        )

    def response(self, question, base64_image):
        params = {
            "model": self.model_name,
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type(base64_image),
                                "data": base64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": f"{question}\n(Answer in {self.language}, "
                            "in one or two short sentences.)",
                        },
                    ],
                }
            ],
        }
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        try:
            if self.use_fallbacks:
                # A declined request is re-run on Anthropic's recommended
                # fallback model inside the same call.
                message = self.client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **params
                )
            else:
                message = self.client.messages.create(**params)
        except anthropic.AuthenticationError:
            logger.bind(tag=TAG).error("Claude vision: the API key was rejected (401)")
            raise
        except anthropic.RateLimitError:
            logger.bind(tag=TAG).error("Claude vision: rate limited (429)")
            raise
        except anthropic.APIStatusError as e:
            logger.bind(tag=TAG).error(f"Claude vision: API error {e.status_code}: {e.message}")
            raise
        except anthropic.APIConnectionError as e:
            logger.bind(tag=TAG).error(f"Claude vision: cannot reach the API: {e}")
            raise

        if message.stop_reason == "refusal":
            return "Sorry, I can't describe that picture."
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        return text or "I couldn't make out the picture."
