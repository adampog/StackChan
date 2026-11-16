"""LLM switchboard for xiaozhi-esp32-server: several brains, one active.

Mounted into the container as core/providers/llm/router/router.py and selected
with "type: router". It holds a set of named presets (local, claude,
openrouter, ...) and sends each request to the active one.

The active preset is the name in a small state file (data/llm-preset). It is
read on every request, so a switch takes effect on the next sentence with no
restart:
  - ./switch.sh llm claude        writes the file from the host
  - "switch to Claude"            said to the robot, handled here

Everything happens on the voice server. Nothing is added to the robot's
firmware or to its tool list.

If the active brain fails before it has said anything (no internet, bad key),
the turn is answered by the fallback preset instead.
"""

import os
import re
import threading

from config.logger import setup_logging
from core.providers.llm.base import LLMProviderBase
from core.utils import llm as llm_utils

TAG = __name__
logger = setup_logging()

PLACEHOLDER_KEYS = ("", "changeme", "none", "null")
MAX_COMMAND_WORDS = 9

_SWITCH = re.compile(r"\b(?:switch|change|swap|go|move)\b.*?\bto\b(.*)$")
_USE = re.compile(r"^(?:please )?use (?:your |the |my )?(.+?)(?: brain| model| mode)?(?: please)?$")
_WHICH = re.compile(r"\b(?:which|what)\b.*\b(?:brain|model|llm)\b")


def _words(text):
    """Lower-case text with punctuation removed and spaces squeezed."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).split())


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.presets = {
            str(name): dict(cfg)
            for name, cfg in (config.get("presets") or {}).items()
            if isinstance(cfg, dict)
        }
        if not self.presets:
            raise ValueError("router: no presets configured")
        self.default = str(config.get("default") or "")
        if self.default not in self.presets:
            self.default = next(iter(self.presets))
        self.fallback = str(config.get("fallback") or "")
        if self.fallback not in self.presets:
            self.fallback = ""
        self.state_file = config.get("state_file") or "data/llm-preset"
        self.voice_switch = str(config.get("voice_switch", True)).lower() in ("true", "1", "yes")

        # (spoken alias, preset), longest alias first so "open router free"
        # wins over "open router".
        aliases = []
        for name, cfg in self.presets.items():
            said = cfg.get("says") or []
            if isinstance(said, str):
                said = said.split(",")
            for alias in [name, name.replace("-", " ")] + list(said):
                alias = _words(alias)
                if alias:
                    aliases.append((alias, name))
        self.aliases = sorted(set(aliases), key=lambda a: -len(a[0]))

        self._instances = {}
        self._lock = threading.Lock()
        self._announced = set()  # presets whose failure was already spoken
        logger.bind(tag=TAG).info(
            f"router: presets {', '.join(self.presets)}; active {self.active()}"
        )

    # -- presets -------------------------------------------------------------

    def label(self, name):
        return str(self.presets[name].get("label") or name)

    def usable(self, name):
        cfg = self.presets[name]
        if "api_key" not in cfg or str(cfg.get("needs_key", True)).lower() in ("false", "0", "no"):
            return True
        return str(cfg.get("api_key") or "").strip().lower() not in PLACEHOLDER_KEYS

    def active(self):
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                name = f.read().strip()
        except OSError:
            name = ""
        return name if name in self.presets else self.default

    def set_active(self, name):
        tmp = f"{self.state_file}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(name + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.state_file)
        logger.bind(tag=TAG).info(f"router: active preset is now {name}")

    def provider(self, name):
        with self._lock:
            if name not in self._instances:
                cfg = self.presets[name]
                self._instances[name] = llm_utils.create_instance(cfg.get("type") or "openai", cfg)
            return self._instances[name]

    # -- spoken commands -----------------------------------------------------

    def command(self, dialogue):
        """A spoken reply if the last user turn is a switch command, else None."""
        if not self.voice_switch or not dialogue:
            return None
        last = dialogue[-1]
        if last.get("role") != "user" or not isinstance(last.get("content"), str):
            return None
        said = _words(last["content"])
        if not said or len(said.split()) > MAX_COMMAND_WORDS:
            return None

        target = None
        match = _SWITCH.search(said)
        if match:
            rest = f" {match.group(1).strip()} "
            for alias, name in self.aliases:
                if f" {alias} " in rest:
                    target = name
                    break
        else:
            match = _USE.match(said)
            if match:
                wanted = match.group(1).strip()
                target = next((n for a, n in self.aliases if a == wanted), None)

        if target is None:
            if _WHICH.search(said) and " you" in f" {said}":
                return f"I'm using {self.label(self.active())}."
            return None

        if not self.usable(target):
            return f"I don't have a key for {self.label(target)} yet."
        if target == self.active():
            return f"I'm already using {self.label(target)}."
        try:
            self.set_active(target)
        except OSError as e:
            logger.bind(tag=TAG).error(f"router: cannot write {self.state_file}: {e}")
            return "Sorry, I couldn't switch."
        return f"Okay, I'm using {self.label(target)} now."

    # -- requests ------------------------------------------------------------

    def _run(self, call, pack):
        """Run call(provider) on the active preset, falling back for this turn
        if it fails before producing anything. pack() shapes a spoken notice."""
        name = self.active()
        started = False
        try:
            for item in call(self.provider(name)):
                started = True
                yield item
            self._announced.discard(name)
            return
        except Exception as e:
            if started or not self.fallback or name == self.fallback:
                raise
            logger.bind(tag=TAG).error(
                f"router: {name} failed ({type(e).__name__}: {e}); "
                f"answering with {self.fallback}"
            )
        if name not in self._announced:
            self._announced.add(name)
            yield pack(
                f"{self.label(name)} isn't answering, "
                f"so I'm using {self.label(self.fallback)} for now. "
            )
        for item in call(self.provider(self.fallback)):
            yield item

    def response(self, session_id, dialogue, **kwargs):
        reply = self.command(dialogue)
        if reply is not None:
            yield reply
            return
        yield from self._run(
            lambda p: p.response(session_id, dialogue, **kwargs),
            lambda text: text,
        )

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        reply = self.command(dialogue)
        if reply is not None:
            yield reply, None
            return
        for item in self._run(
            lambda p: p.response_with_functions(session_id, dialogue, functions=functions, **kwargs),
            lambda text: (text, None),
        ):
            # The voice server tests `"content" in response` on this pair; a
            # text chunk that is exactly "content" would trip it.
            if isinstance(item, tuple) and len(item) == 2 and item[0] == "content":
                item = ("content ", item[1])
            yield item
