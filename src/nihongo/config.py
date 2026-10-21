"""配置与凭据。

凭据只从环境变量或私有的 ``credentials.env`` 读取，绝不写进代码。
环境变量优先于 ``credentials.env``。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODELS = {
    # 老师对话、语法讲解、例句生成等纯文字任务
    "chat": "gpt-6-astra",
    # 发音评测、听写：通过 /v1/chat/completions 的 input_audio 发送录音
    "audio": "doubao-seed-2-1-lite-260915",
    # 响应要快的小任务（单词释义、例句）
    "fast": "doubao-seed-2-1-lite-260915",
}


def config_home() -> Path:
    return Path(os.environ.get("NIHONGO_CONFIG_HOME", Path.home() / ".config" / "nihongo"))


def data_home() -> Path:
    return Path(os.environ.get("NIHONGO_DATA_HOME", Path.home() / ".local" / "share" / "nihongo"))


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
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    timeout: float = 90.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def public(self) -> dict:
        """给前端展示的配置，不含密钥。"""
        return {
            "configured": self.configured,
            "base_url": self.base_url,
            "caller": self.caller,
            "has_api_key": bool(self.api_key),
            "models": self.models,
        }


def load_settings() -> Settings:
    home = config_home()
    creds = _read_env_file(home / "credentials.env")

    def get(name: str) -> str:
        return os.environ.get(name) or creds.get(name, "")

    models = dict(DEFAULT_MODELS)
    toml_path = home / "config.toml"
    timeout = 90.0
    if toml_path.is_file():
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        models.update({k: str(v) for k, v in data.get("models", {}).items()})
        timeout = float(data.get("timeout", timeout))
    for role in list(models):
        override = get(f"NIHONGO_MODEL_{role.upper()}")
        if override:
            models[role] = override

    return Settings(
        base_url=get("NIHONGO_TEXT_BASE_URL").rstrip("/"),
        api_key=get("NIHONGO_TEXT_API_KEY"),
        caller=get("NIHONGO_TEXT_CALLER"),
        models=models,
        timeout=timeout,
    )


CREDENTIAL_KEYS = ("NIHONGO_TEXT_BASE_URL", "NIHONGO_TEXT_API_KEY", "NIHONGO_TEXT_CALLER")


def save_credentials(values: dict[str, str], models: dict[str, str] | None = None) -> list[str]:
    """把应用内填写的凭据写入私有的 credentials.env（权限 600），模型名写入 config.toml。

    空字符串表示保留原值。返回被环境变量覆盖、因而写入后不会生效的变量名。
    """
    home = config_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / "credentials.env"
    current = _read_env_file(path)
    for key in CREDENTIAL_KEYS:
        value = (values.get(key) or "").strip()
        if value:
            current[key] = value
    lines = ["# 日语学习助手模型凭据（由应用写入，请勿提交到版本库）"]
    lines += [f"{k}={v}" for k, v in current.items()]
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if models:
        toml_path = home / "config.toml"
        data = tomllib.loads(toml_path.read_text("utf-8")) if toml_path.is_file() else {}
        merged = {
            **data.get("models", {}),
            **{k: v.strip() for k, v in models.items() if v.strip()},
        }
        out = [f"timeout = {float(data.get('timeout', 90.0))}", "", "[models]"]
        out += [f'{k} = "{v}"' for k, v in merged.items() if k.isidentifier() and '"' not in v]
        toml_path.write_text("\n".join(out) + "\n", encoding="utf-8")

    shadowed = [k for k in CREDENTIAL_KEYS if os.environ.get(k)]
    if models:
        shadowed += [
            f"NIHONGO_MODEL_{r.upper()}"
            for r in models
            if os.environ.get(f"NIHONGO_MODEL_{r.upper()}")
        ]
    return shadowed
