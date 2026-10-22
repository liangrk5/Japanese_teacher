"""配置与凭据。

凭据只从环境变量或私有的 ``credentials.env`` 读取，绝不写进代码。
优先级：环境变量 > ~/.config/rulebutterfly/credentials.env > ~/.config/turing/credentials.env
> ~/.config/werewolf/credentials.env > ~/.config/nihongo/credentials.env
（后几个让已经配好其他游戏或日语助手的用户不用再填一次）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

SEED = "doubao-seed-2-1-pro-260915"
ASTRA = "gpt-6-astra"


def config_home() -> Path:
    return Path(os.environ.get("TOWN_CONFIG_HOME", Path.home() / ".config" / "rulebutterfly"))


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        values[key] = value.strip().strip("'\"")
    return values


@dataclass
class Settings:
    base_url: str = ""
    api_key: str = ""
    caller: str = ""
    models: list[str] = field(default_factory=lambda: [SEED, ASTRA])
    # 这些模型只接受默认 temperature，请求里不带这个参数
    no_temperature: set[str] = field(default_factory=lambda: {ASTRA})
    temperature: float = 0.85
    # 单次模型调用的墙钟上限，超时用兜底回答，保证对局不会卡死
    timeout: float = 60.0
    # 名字里带这些前缀的模型请求时关闭深度思考（豆包 Seed 默认会先长时间推理）
    no_thinking_prefixes: tuple[str, ...] = ("doubao-seed",)
    concurrency: int = 8

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def public(self) -> dict:
        """给前端展示的配置，不含密钥。"""
        return {
            "configured": self.configured,
            "base_url": self.base_url,
            "models": self.models,
        }


def load_settings() -> Settings:
    creds = _read_env_file(Path.home() / ".config" / "nihongo" / "credentials.env")
    creds.update(_read_env_file(Path.home() / ".config" / "werewolf" / "credentials.env"))
    creds.update(_read_env_file(Path.home() / ".config" / "turing" / "credentials.env"))
    creds.update(_read_env_file(config_home() / "credentials.env"))

    def get(*names: str) -> str:
        for name in names:
            value = os.environ.get(name) or creds.get(name, "")
            if value:
                return value
        return ""

    s = Settings(
        base_url=get("TOWN_BASE_URL", "TURING_BASE_URL", "WEREWOLF_BASE_URL", "NIHONGO_TEXT_BASE_URL").rstrip("/"),
        api_key=get("TOWN_API_KEY", "TURING_API_KEY", "WEREWOLF_API_KEY", "NIHONGO_TEXT_API_KEY"),
        caller=get("TOWN_CALLER", "TURING_CALLER", "WEREWOLF_CALLER", "NIHONGO_TEXT_CALLER"),
    )
    if models := get("TOWN_MODELS"):
        s.models = [m.strip() for m in models.split(",") if m.strip()]
    if extra := get("TOWN_NO_TEMPERATURE_MODELS"):
        s.no_temperature |= {m.strip() for m in extra.split(",") if m.strip()}
    if get("TOWN_KEEP_THINKING").lower() in ("1", "true", "yes"):
        s.no_thinking_prefixes = ()
    if timeout := get("TOWN_TIMEOUT"):
        s.timeout = float(timeout)
    return s
