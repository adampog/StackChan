"""OpenAI-compatible LLM provider for llama.cpp-style servers (Strata, llama-server).

Mounted into the xiaozhi-esp32-server container as
core/providers/llm/llamacpp/llamacpp.py and selected with `type: llamacpp`.

It is the stock `openai` provider with one difference: thinking is switched
off through the chat template (`chat_template_kwargs.enable_thinking=false`),
which is the form these servers honour. The stock provider sends
`{"thinking": {"type": "disabled"}}`, which they ignore, so a reasoning model
spends seconds (or its whole token budget) thinking before it speaks.

Config keys (on top of the openai ones):
  thinking: off | keep      off (default) disables reasoning; keep sends nothing
"""

from core.providers.llm.openai.openai import LLMProvider as OpenAILLMProvider

OFF_MODES = ("", "off", "disabled", "disable", "none", "false")


class LLMProvider(OpenAILLMProvider):
    def _apply_thinking_disabled(self, request_params: dict):
        mode = getattr(self, "thinking_mode", "")
        if mode not in OFF_MODES:
            return
        extra_body = request_params.setdefault("extra_body", {})
        extra_body.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
