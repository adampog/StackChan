"""Vision provider for OpenAI-compatible servers, tuned for StackChan.

Mounted into the xiaozhi-esp32-server container as
core/providers/vllm/llamacpp.py and selected with `type: llamacpp`.

Differences from the stock `openai` vision provider:
  - it does not append "(请使用中文回复)" (answer in Chinese) to the question;
    it asks for a short answer in the configured `language` instead
  - `thinking: off` disables reasoning through the chat template
    (`chat_template_kwargs.enable_thinking=false`), which is what llama.cpp-style
    servers such as Strata honour; `thinking: keep` sends nothing (cloud APIs)
  - `max_tokens` is actually sent, so a reply can't run away
"""

from config.logger import setup_logging
from core.providers.vllm.openai import VLLMProvider as OpenAIVLLMProvider

TAG = __name__
logger = setup_logging()

OFF_MODES = ("", "off", "disabled", "disable", "none", "false")


class VLLMProvider(OpenAIVLLMProvider):
    def __init__(self, config):
        super().__init__(config)
        self.language = config.get("language") or "English"
        self.thinking_mode = str(config.get("thinking") or "").strip().lower()

    def response(self, question, base64_image):
        question = f"{question}\n(Answer in {self.language}, in one or two short sentences.)"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                ],
            }
        ]
        params = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "max_tokens": self.max_tokens,
        }
        if self.thinking_mode in OFF_MODES:
            params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        try:
            response = self.client.chat.completions.create(**params)
            return response.choices[0].message.content
        except Exception as e:
            logger.bind(tag=TAG).error(f"Error in response generation: {e}")
            raise
