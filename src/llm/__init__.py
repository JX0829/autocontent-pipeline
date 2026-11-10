"""src/llm —— LLM 统一调用层（T1.5）

所有模型调用必须经过 `LLMClient`：模型名/预算/超时只从 `pipeline.yaml` 槽位读取，
调用方不得自定 `max_tokens`，失败必须抛出并留痕，禁止静默兜底。
"""
from src.llm.client import (  # noqa: F401
    ClassificationError,
    ConfigError,
    EmptyCompletion,
    LLMClient,
    LLMError,
    OutputTooShort,
    TruncatedOutput,
    Usage,
    record_degraded,
)

__all__ = [
    "LLMClient", "LLMError", "ConfigError", "TruncatedOutput",
    "EmptyCompletion", "OutputTooShort", "ClassificationError", "Usage", "record_degraded",
]
