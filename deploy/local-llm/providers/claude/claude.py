"""Claude LLM provider for xiaozhi-esp32-server, using the official Anthropic SDK.

Mounted into the container as core/providers/llm/claude/claude.py and selected
with "type: claude". The voice server keeps the conversation in OpenAI chat
format, so this file converts that history (and the robot's tool list) to the
Messages API on every request and streams the reply back.

Needs the "anthropic" package in the image (see ../../Dockerfile).
"""

import json
import re

import anthropic

from config.logger import setup_logging
from core.providers.llm.base import LLMProviderBase

TAG = __name__
logger = setup_logging()

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that take the server-side refusal fallback ("fallbacks": "default").
FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")
# Models where thinking cannot be turned off (the API returns a 400).
THINKING_ALWAYS_ON_PREFIXES = ("claude-opus-5-5", "claude-fable-5", "claude-mythos-5")
# Tools the voice server adds for models that prefer calling a function over
# answering in text. Claude answers in text, so these are left out.
SKIPPED_TOOLS = ("direct_answer",)

_NAME_OK = re.compile(r"[^a-zA-Z0-9_-]")


class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    """Shaped like an OpenAI tool-call delta, which the voice server expects."""

    def __init__(self, index, call_id, name, arguments):
        self.index = index
        self.id = call_id
        self.type = "function"
        self.function = _Function(name, arguments)


def _safe_name(name):
    return _NAME_OK.sub("_", str(name or "tool"))[:64] or "tool"


def _safe_id(call_id, n):
    cleaned = _NAME_OK.sub("_", str(call_id or ""))
    return cleaned or f"call_{n}"


def _text(content):
    """OpenAI message content (string, None or a list of parts) as plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    return str(content)


def convert_tools(functions):
    """OpenAI tool list -> (Anthropic tool list, {sent name: original name})."""
    tools, names = [], {}
    for item in functions or []:
        fn = item.get("function") if isinstance(item, dict) else None
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        if fn["name"] in SKIPPED_TOOLS:
            continue
        schema = fn.get("parameters")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        name = _safe_name(fn["name"])
        names[name] = fn["name"]
        tools.append(
            {
                "name": name,
                "description": str(fn.get("description") or name),
                "input_schema": schema,
                # Tool inputs stream as they are generated; the server then no
                # longer validates them, so valid_input() checks them here.
                "eager_input_streaming": True,
            }
        )
    if tools:
        # The tool list is the same on every turn, so let the API cache it.
        tools[-1]["cache_control"] = {"type": "ephemeral"}
    return tools, names


def convert_dialogue(dialogue, keep_tools=True):
    """OpenAI chat history -> (system text, Anthropic messages).

    Every assistant tool call is followed at once by its result, wherever the
    result sits in the history (the server appends some at the end), and
    neighbouring turns of one role are merged, as the Messages API requires.
    With keep_tools=False (a request that sends no tool list) earlier tool
    calls are left out and their results are kept as plain text.
    """
    system_parts = []
    results = {}  # tool_call_id -> result text
    for msg in dialogue:
        if msg.get("role") == "tool" and msg.get("tool_call_id") is not None:
            results[str(msg["tool_call_id"])] = _text(msg.get("content"))

    turns = []  # [role, [content blocks]]

    def add(role, blocks):
        blocks = [b for b in blocks if b]
        if not blocks:
            return
        if turns and turns[-1][0] == role:
            turns[-1][1].extend(blocks)
        else:
            turns.append([role, blocks])

    n = 0
    for msg in dialogue:
        role = msg.get("role")
        text = _text(msg.get("content")).strip()
        if role == "system":
            if text:
                system_parts.append(text)
        elif role == "user":
            add("user", [{"type": "text", "text": text}] if text else [])
        elif role == "assistant":
            blocks = [{"type": "text", "text": text}] if text else []
            answers = []
            for call in msg.get("tool_calls") or []:
                fn = (call.get("function") if isinstance(call, dict) else None) or {}
                if fn.get("name") in SKIPPED_TOOLS:
                    continue
                n += 1
                raw_id = call.get("id")
                call_id = _safe_id(raw_id, n)
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                if not keep_tools:
                    result = results.get(str(raw_id))
                    if result:
                        answers.append({"type": "text", "text": f"(Tool result: {result})"})
                    continue
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": _safe_name(fn.get("name")),
                        "input": args,
                    }
                )
                answers.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": results.get(str(raw_id)) or "(no result)",
                    }
                )
            add("assistant", blocks)
            if answers:
                add("user", answers)
        # role == "tool": already attached to its call above

    if not turns or turns[0][0] != "user":
        turns.insert(0, ["user", [{"type": "text", "text": "Hello."}]])
    if turns[-1][0] != "user":
        turns.append(["user", [{"type": "text", "text": "Please continue."}]])

    messages = []
    for role, blocks in turns:
        if role == "user":
            # Tool results must come first in a user turn.
            blocks = [b for b in blocks if b["type"] == "tool_result"] + [
                b for b in blocks if b["type"] != "tool_result"
            ]
        messages.append({"role": role, "content": blocks})
    return "\n\n".join(system_parts), messages


_JSON_TYPES = {
    "string": str,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def valid_input(args, schema):
    """Light schema check on a streamed tool input: an object with the
    required keys and matching basic types."""
    if not isinstance(args, dict):
        return False
    schema = schema if isinstance(schema, dict) else {}
    for key in schema.get("required") or []:
        if key not in args:
            return False
    props = schema.get("properties") or {}
    for key, value in args.items():
        kind = (props.get(key) or {}).get("type") if isinstance(props.get(key), dict) else None
        if kind in ("integer", "number"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            if kind == "integer" and isinstance(value, float) and not value.is_integer():
                return False
        elif kind in _JSON_TYPES and not isinstance(value, _JSON_TYPES[kind]):
            return False
    return True


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.model_name = config.get("model_name") or "claude-opus-5-5"
        self.effort = str(config.get("effort") or "").strip().lower()
        self.thinking_mode = str(config.get("thinking") or "").strip().lower()
        try:
            self.max_tokens = int(config.get("max_tokens") or 4096)
        except (TypeError, ValueError):
            self.max_tokens = 4096
        try:
            timeout = float(config.get("timeout") or 60)
        except (TypeError, ValueError):
            timeout = 60.0

        mode = str(config.get("fallbacks") or "auto").strip().lower()
        if mode in ("on", "true", "yes", "default"):
            self.use_fallbacks = True
        elif mode in ("off", "false", "no", "none"):
            self.use_fallbacks = False
        else:
            self.use_fallbacks = self.model_name.startswith(FALLBACK_MODEL_PREFIXES)

        api_key = config.get("api_key")
        if not api_key or api_key == "changeme":
            logger.bind(tag=TAG).error("Claude: no API key set (ANTHROPIC_API_KEY in .env)")
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1)

    # -- request -----------------------------------------------------------

    def _params(self, dialogue, tools):
        system, messages = convert_dialogue(dialogue, keep_tools=bool(tools))
        params = {
            "model": self.model_name,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        if system:
            params["system"] = system
        if tools:
            params["tools"] = tools
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        if self.thinking_mode in ("off", "disabled", "disable", "none", "false"):
            if self.model_name.startswith(THINKING_ALWAYS_ON_PREFIXES):
                logger.bind(tag=TAG).debug(
                    f"Claude: thinking is always on for {self.model_name}; using effort instead"
                )
            else:
                params["thinking"] = {"type": "disabled"}
        if self.use_fallbacks:
            # If the model declines a request, the API re-runs it on the
            # fallback model Anthropic recommends, inside the same call.
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _open(self, params):
        if "betas" in params:
            return self.client.beta.messages.stream(**params)
        return self.client.messages.stream(**params)

    def _stream(self, dialogue, tools):
        """Yield ("text", str) while the reply streams, then ("final", message)."""
        params = self._params(dialogue, tools)
        try:
            with self._open(params) as stream:
                for event in stream:
                    if event.type == "text" and event.text:
                        yield "text", event.text
                final = stream.get_final_message()
        except anthropic.AuthenticationError:
            logger.bind(tag=TAG).error("Claude: the API key was rejected (401)")
            raise
        except anthropic.RateLimitError:
            logger.bind(tag=TAG).error("Claude: rate limited (429)")
            raise
        except anthropic.APIStatusError as e:
            logger.bind(tag=TAG).error(f"Claude: API error {e.status_code}: {e.message}")
            raise
        except anthropic.APIConnectionError as e:
            logger.bind(tag=TAG).error(f"Claude: cannot reach the API: {e}")
            raise
        usage = getattr(final, "usage", None)
        logger.bind(tag=TAG).info(
            f"Claude {final.model}: stop={final.stop_reason}, "
            f"in={getattr(usage, 'input_tokens', '?')}, out={getattr(usage, 'output_tokens', '?')}"
        )
        yield "final", final

    # -- voice server interface --------------------------------------------

    def response(self, session_id, dialogue, **kwargs):
        spoke = False
        for kind, value in self._stream(dialogue, None):
            if kind == "text":
                spoke = True
                yield value
            elif value.stop_reason == "refusal" and not spoke:
                yield "Sorry, I can't help with that one."

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        tools, names = convert_tools(functions)
        schemas = {t["name"]: t["input_schema"] for t in tools}

        for attempt in (1, 2):
            spoke = False
            final = None
            try:
                for kind, value in self._stream(dialogue, tools):
                    if kind == "text":
                        spoke = True
                        yield value, None
                    else:
                        final = value
            except ValueError as e:
                # A streamed tool input the SDK could not parse at all. Nothing
                # was run; ask again once, unless part of the reply was spoken.
                logger.bind(tag=TAG).warning(f"Claude: unreadable tool input ({e})")
                if attempt == 1 and not spoke:
                    continue
                if not spoke:
                    yield "Sorry, I lost my train of thought. Please ask again.", None
                return

            tool_uses = [b for b in final.content if b.type == "tool_use"]
            if final.stop_reason == "refusal":
                # A refusal can cut a tool call off mid-input: run nothing.
                if not spoke:
                    yield "Sorry, I can't help with that one.", None
                return
            if not tool_uses:
                return
            if final.stop_reason == "max_tokens":
                # A cut-off tool input parses as a valid partial object: run nothing.
                logger.bind(tag=TAG).warning("Claude: reply cut off inside a tool call")
                if not spoke:
                    yield "Sorry, that got cut short. Please ask again.", None
                return

            if not all(valid_input(b.input, schemas.get(b.name)) for b in tool_uses):
                logger.bind(tag=TAG).warning("Claude: tool input failed validation")
                if attempt == 1 and not spoke:
                    continue
                if not spoke:
                    yield "Sorry, I got that wrong. Please ask again.", None
                return

            calls = [
                _ToolCall(
                    i,
                    b.id,
                    names.get(b.name, b.name),
                    json.dumps(b.input, ensure_ascii=False),
                )
                for i, b in enumerate(tool_uses)
            ]
            yield None, calls
            return
