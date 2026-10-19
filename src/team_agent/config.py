"""配置：四个 Agent 的模型、API、预算与限流。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

DEFAULT_MAX_CALLS_PER_TASK = 12
DEFAULT_MAX_TOTAL_CALLS = 50


def _str(source: Mapping[str, str], name: str, default: str) -> str:
    value = (source.get(name) or "").strip()
    return value or default


def _int(source: Mapping[str, str], name: str, default: int) -> int:
    raw = (source.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数，收到 {raw!r}") from exc


def _float(source: Mapping[str, str], name: str, default: float) -> float:
    raw = (source.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是数字，收到 {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class ModelConfig:
    role: str
    provider: str
    api_base: str
    api_key: str = field(repr=False)
    model: str
    temperature: float = 0.0
    min_interval_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not self.api_base.startswith(("http://", "https://")):
            raise ConfigError(f"{self.role} 的 API_BASE 必须以 http(s):// 开头")
        if not self.model:
            raise ConfigError(f"{self.role} 的 model 不能为空")
        if not self.api_key:
            raise ConfigError(f"{self.role} 缺少 API key（环境变量）")
        if self.min_interval_seconds < 0:
            raise ConfigError("min_interval_seconds 不能为负")


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path = Path("data/collab.db")
    seed_dir: Path = Path("seed_data")
    planner: ModelConfig | None = None
    business: ModelConfig | None = None
    policy: ModelConfig | None = None
    auditor: ModelConfig | None = None
    max_calls_per_task: int = DEFAULT_MAX_CALLS_PER_TASK
    max_total_calls: int = DEFAULT_MAX_TOTAL_CALLS
    max_prompt_chars: int = 30000
    max_completion_tokens: int = 6000
    revision_limit: int = 1
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.max_calls_per_task < 1:
            raise ConfigError("max_calls_per_task 必须大于 0")
        if self.max_total_calls < self.max_calls_per_task:
            raise ConfigError("max_total_calls 不能小于单任务上限")
        if not 0 <= self.revision_limit <= 3:
            raise ConfigError("revision_limit 必须在 0..3 之间")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, require_keys: bool = True) -> Settings:
        source = os.environ if env is None else env

        def key(name: str) -> str:
            value = (source.get(name) or "").strip()
            if value:
                return value
            if require_keys:
                raise ConfigError(f"缺少环境变量 {name}（参考 .env.example）")
            return "unset-key"

        return cls(
            db_path=Path(_str(source, "COLLAB_DB_PATH", "data/collab.db")),
            seed_dir=Path(_str(source, "COLLAB_SEED_DIR", "seed_data")),
            planner=ModelConfig(
                role="planner",
                provider="deepseek",
                api_base=_str(source, "DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"),
                api_key=key("DEEPSEEK_API_KEY"),
                model=_str(source, "PLANNER_MODEL", "deepseek-flash"),
                temperature=_float(source, "PLANNER_TEMPERATURE", 0.0),
                min_interval_seconds=_float(source, "DEEPSEEK_MIN_INTERVAL_SECONDS", 0.0),
            ),
            business=ModelConfig(
                role="business",
                provider="qwen",
                api_base=_str(source, "QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                api_key=key("QWEN_API_KEY"),
                model=_str(source, "BUSINESS_MODEL", "qwen3.8-flash"),
                temperature=_float(source, "BUSINESS_TEMPERATURE", 0.0),
                min_interval_seconds=_float(source, "QWEN_MIN_INTERVAL_SECONDS", 0.0),
            ),
            policy=ModelConfig(
                role="policy",
                provider="qwen",
                api_base=_str(source, "QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                api_key=key("QWEN_API_KEY"),
                model=_str(source, "POLICY_MODEL", "qwen3.8-flash"),
                temperature=_float(source, "POLICY_TEMPERATURE", 0.0),
                min_interval_seconds=_float(source, "QWEN_MIN_INTERVAL_SECONDS", 0.0),
            ),
            auditor=ModelConfig(
                role="auditor",
                provider="kimi",
                api_base=_str(source, "KIMI_API_BASE", "https://api.moonshot.cn/v1"),
                api_key=key("KIMI_API_KEY"),
                # Kimi 只接受 temperature=1
                model=_str(source, "AUDITOR_MODEL", "kimi-k2.7-code"),
                temperature=_float(source, "AUDITOR_TEMPERATURE", 1.0),
                min_interval_seconds=_float(source, "KIMI_MIN_INTERVAL_SECONDS", 21.0),
            ),
            max_calls_per_task=_int(source, "COLLAB_MAX_CALLS_PER_TASK", DEFAULT_MAX_CALLS_PER_TASK),
            max_total_calls=_int(source, "COLLAB_MAX_TOTAL_CALLS", DEFAULT_MAX_TOTAL_CALLS),
            max_prompt_chars=_int(source, "COLLAB_MAX_PROMPT_CHARS", 30000),
            max_completion_tokens=_int(source, "COLLAB_MAX_COMPLETION_TOKENS", 6000),
            revision_limit=_int(source, "COLLAB_REVISION_LIMIT", 1),
            log_level=_str(source, "COLLAB_LOG_LEVEL", "INFO").upper(),
        )
