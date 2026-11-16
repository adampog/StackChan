"""Vision switchboard for xiaozhi-esp32-server: several vision backends, one active.

Mounted into the container as core/providers/vllm/router.py and selected with
"type: router". The active preset is the name in a small state file
(data/vision-preset), read on every camera question, so
"./switch.sh vision claude" takes effect at once with no restart.
"""

from config.logger import setup_logging
from core.providers.vllm.base import VLLMProviderBase
from core.utils import vllm as vllm_utils

TAG = __name__
logger = setup_logging()


class VLLMProvider(VLLMProviderBase):
    def __init__(self, config):
        self.presets = {
            str(name): dict(cfg)
            for name, cfg in (config.get("presets") or {}).items()
            if isinstance(cfg, dict)
        }
        if not self.presets:
            raise ValueError("vision router: no presets configured")
        self.default = str(config.get("default") or "")
        if self.default not in self.presets:
            self.default = next(iter(self.presets))
        self.state_file = config.get("state_file") or "data/vision-preset"

    def active(self):
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                name = f.read().strip()
        except OSError:
            name = ""
        return name if name in self.presets else self.default

    def response(self, question, base64_image):
        name = self.active()
        cfg = self.presets[name]
        logger.bind(tag=TAG).info(f"vision router: using {name}")
        provider = vllm_utils.create_instance(cfg.get("type") or "openai", cfg)
        return provider.response(question, base64_image)
