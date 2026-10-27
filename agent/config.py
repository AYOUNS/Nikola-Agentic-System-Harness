"""Configuration loader and default values for the agent harness.

Configuration values are defined as frozen dataclasses with defaults.
The YAML config file overrides these defaults.
Secrets (e.g. INNKUBE_ACCESS_TOKEN) are never stored here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class LoopConfig:
    """Configuration for the ReAct loop execution limits.
    
    Defines guard conditions that terminate the loop to prevent infinite execution.
    """
    max_iterations: int = 100
    """Maximum number of reasoning-action-observation cycles before forced termination."""
    
    max_tokens: int | None = 8192
    """Maximum tokens for LLM completions. None = use model default."""


@dataclass(frozen=True)
class ContextConfig:
    """Configuration for context management and compaction.
    
    Controls how messages are kept within the LLM's context window budget.
    """
    window_tokens: int = 262144
    """Total context window size in tokens. Overrides model detection if set."""
    
    reserve_output_tokens: int = 8192
    """Tokens reserved for LLM output. Must be >= loop.max_tokens."""
    
    chars_per_token: float = 3.5
    """Conservative character-to-token estimation ratio for Qwen/Gemma models."""
    
    max_observation_chars: int = 10000
    """Maximum characters in tool observations before truncation (Tier 1 compaction)."""
    
    summary_max_tokens: int = 1500
    """Token budget for the compaction summarization call (Tier 3)."""
    
    summary_reasoning_effort: str = "none"
    """Reasoning effort for summarization ('none' | 'low' | 'medium' | 'high' | '')."""
    
    @property
    def compaction_budget(self) -> int:
        """Derived value: tokens available for input messages after reserving output space.
        
        This is the target budget for context compaction — the number of tokens
        that can be used for message history while ensuring the LLM has enough
        room to generate a complete response.
        """
        return self.window_tokens - self.reserve_output_tokens


@dataclass(frozen=True)
class LLMConfig:
    """Configuration for LLM client and API settings."""
    model: str = "qwen-agentworld-35b-a3b"
    """Default model for main agent."""
    
    embedding_model: str = "octen-embedding-8b"
    """Model for memory embeddings."""
    
    base_url: str = "https://llms.innkube.fim.uni-passau.de/"
    """LLM API base URL. Can be overridden by OPENAI_BASE_URL env var."""
    
    temperature: float = 0.7
    """Sampling temperature for LLM completions."""
    
    timeout_seconds: int = 60
    """HTTP timeout for LLM API calls."""


@dataclass(frozen=True)
class LoggingConfig:
    """Configuration for observability logging."""
    enabled: bool = True
    """Whether observability logging is enabled."""
    
    debug: bool = False
    """Whether to enable debug-level logging with entry/exit tracking."""
    
    file: str | None = None
    """Log file path. Defaults to workspace/.nikola/logs/agent.jsonl if None."""


@dataclass(frozen=True)
class MetricsConfig:
    """Configuration for the Prometheus metrics endpoint."""
    enabled: bool = True
    """Whether to collect Prometheus metrics and serve /metrics."""

    host: str = "0.0.0.0"  # scraped from the Prometheus container
    """Interface the metrics endpoint binds to."""

    port: int = 9464
    """Port the metrics endpoint listens on."""


@dataclass(frozen=True)
class MemoryConfig:
    """Configuration for long-term memory persistence."""
    db_path: str = "/workspace/.agent/memory.db"
    """SQLite database path for memory storage."""
    
    qdrant_url: str = "http://qdrant:6333"
    """Qdrant service URL for vector embeddings."""
    
    qdrant_collection: str = "agent_memories"
    """Qdrant collection name for memory embeddings."""


@dataclass(frozen=True)
class AppConfig:
    """Top-level application configuration.
    
    Combines all sub-configs into a single frozen structure.
    Constructed from YAML config file with defaults for missing values.
    """
    loop: LoopConfig
    context: ContextConfig
    llm: LLMConfig
    memory: MemoryConfig
    logging: LoggingConfig
    metrics: MetricsConfig = MetricsConfig()
    workspace: str = "/workspace"
    """Workspace root directory for sandboxed file operations."""


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    """load_config reads a YAML config file and returns the parsed dict.

    Reads the file at ``path``, parses it with PyYAML safe_load, and returns
    the result. Returns an empty dict if the file is empty. Never reads or
    stores secrets — those live in environment variables only.

    path: filesystem path to the YAML config file. Defaults to ``config.yaml``
          in the current working directory.

    Returns the parsed config as a plain dict. Callers access values with
    standard dict keys, e.g. ``config["llm"]["model"]``.

    Raises FileNotFoundError if the file does not exist at ``path``.

    Example:
        cfg = load_config()                        # uses config.yaml
        cfg = load_config("path/to/config.yaml")   # custom path

    See also: config.yaml for the full list of supported keys.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r") as fh:
        return yaml.safe_load(fh) or {}


def load_app_config(path: str | Path = "config.yaml") -> AppConfig:
    """load_app_config reads YAML and returns a typed AppConfig structure.
    
    Reads the file at ``path``, parses it, and constructs frozen dataclass
    config objects with defaults for missing values.
    
    path: filesystem path to the YAML config file. Defaults to ``config.yaml``
          in the current working directory.
    
    Returns AppConfig with all sub-configs populated from YAML + defaults.
    
    Raises FileNotFoundError if the file does not exist at ``path``.
    
    Example:
        cfg = load_app_config()
        print(cfg.loop.max_iterations)  # 30 (from default or YAML)
        print(cfg.context.compaction_budget)  # computed property
    
    See also: AppConfig, LoopConfig, ContextConfig for available fields.
    """
    raw = load_config(path)
    
    # Build sub-configs from YAML with dataclass defaults for missing keys
    loop_data = raw.get("loop", {})
    loop_cfg = LoopConfig(
        max_iterations=loop_data.get("max_iterations", LoopConfig.max_iterations),
        max_tokens=loop_data.get("max_tokens", LoopConfig.max_tokens),
    )
    
    context_data = raw.get("context", {})
    context_cfg = ContextConfig(
        window_tokens=context_data.get("window_tokens", ContextConfig.window_tokens),
        reserve_output_tokens=context_data.get("reserve_output_tokens", ContextConfig.reserve_output_tokens),
        chars_per_token=context_data.get("chars_per_token", ContextConfig.chars_per_token),
        max_observation_chars=context_data.get("max_observation_chars", ContextConfig.max_observation_chars),
        summary_max_tokens=context_data.get("summary_max_tokens", ContextConfig.summary_max_tokens),
        summary_reasoning_effort=context_data.get("summary_reasoning_effort", ContextConfig.summary_reasoning_effort),
    )
    
    llm_data = raw.get("llm", {})
    llm_cfg = LLMConfig(
        model=llm_data.get("model", LLMConfig.model),
        embedding_model=llm_data.get("embedding_model", LLMConfig.embedding_model),
        base_url=llm_data.get("base_url", LLMConfig.base_url),
        temperature=llm_data.get("temperature", LLMConfig.temperature),
        timeout_seconds=llm_data.get("timeout_seconds", LLMConfig.timeout_seconds),
    )
    
    memory_data = raw.get("memory", {})
    qdrant_data = memory_data.get("qdrant", {})
    memory_cfg = MemoryConfig(
        db_path=memory_data.get("db_path", MemoryConfig.db_path),
        qdrant_url=qdrant_data.get("url", MemoryConfig.qdrant_url),
        qdrant_collection=qdrant_data.get("collection", MemoryConfig.qdrant_collection),
    )
    
    logging_data = raw.get("logging", {})
    logging_cfg = LoggingConfig(
        enabled=logging_data.get("enabled", LoggingConfig.enabled),
        debug=logging_data.get("debug", LoggingConfig.debug),
        file=logging_data.get("file", LoggingConfig.file),
    )
    
    metrics_data = raw.get("metrics", {})
    metrics_cfg = MetricsConfig(
        enabled=metrics_data.get("enabled", MetricsConfig.enabled),
        host=metrics_data.get("host", MetricsConfig.host),
        port=metrics_data.get("port", MetricsConfig.port),
    )

    return AppConfig(
        loop=loop_cfg,
        context=context_cfg,
        llm=llm_cfg,
        memory=memory_cfg,
        logging=logging_cfg,
        metrics=metrics_cfg,
        workspace=raw.get("workspace", AppConfig.workspace),
    )


def with_model_override(cfg: AppConfig, model: str) -> AppConfig:
    """Return new AppConfig with overridden model.
    
    Creates a new frozen config with the specified model, preserving all
    other values. Used for CLI --model override.
    
    Args:
        cfg: Original config
        model: Model name to override with
        
    Returns:
        New AppConfig with updated model
        
    Example:
        cfg = load_app_config()
        cfg = with_model_override(cfg, "gemma4-31b-it")
    """
    new_llm = replace(cfg.llm, model=model)
    return replace(cfg, llm=new_llm)


def with_context_override(cfg: AppConfig, context: ContextConfig) -> AppConfig:
    """Return new AppConfig with overridden context config.
    
    Creates a new frozen config with the specified context config, preserving
    all other values. Used when dynamic model info provides actual window size.
    
    Args:
        cfg: Original config
        context: New context configuration
        
    Returns:
        New AppConfig with updated context
        
    Example:
        cfg = load_app_config()
        dynamic_ctx = ContextConfig(window_tokens=262144, ...)
        cfg = with_context_override(cfg, dynamic_ctx)
    """
    return replace(cfg, context=context)


def to_dict(cfg: AppConfig) -> dict[str, Any]:
    """Convert AppConfig to legacy dict format.
    
    Converts the typed config structure back to the old dict format
    for compatibility with code that hasn't been migrated yet.
    
    Args:
        cfg: Typed config
        
    Returns:
        Dict in the old format
        
    Example:
        cfg = load_app_config()
        legacy_dict = to_dict(cfg)
        model = legacy_dict["llm"]["model"]
    """
    return {
        "llm": {
            "model": cfg.llm.model,
            "embedding_model": cfg.llm.embedding_model,
            "base_url": cfg.llm.base_url,
            "temperature": cfg.llm.temperature,
            "timeout_seconds": cfg.llm.timeout_seconds,
        },
        "loop": {
            "max_iterations": cfg.loop.max_iterations,
            "max_tokens": cfg.loop.max_tokens,
        },
        "context": {
            "window_tokens": cfg.context.window_tokens,
            "reserve_output_tokens": cfg.context.reserve_output_tokens,
            "chars_per_token": cfg.context.chars_per_token,
            "max_observation_chars": cfg.context.max_observation_chars,
            "summary_max_tokens": cfg.context.summary_max_tokens,
            "summary_reasoning_effort": cfg.context.summary_reasoning_effort,
        },
        "memory": {
            "db_path": cfg.memory.db_path,
            "qdrant": {
                "url": cfg.memory.qdrant_url,
                "collection": cfg.memory.qdrant_collection,
            },
        },
        "logging": {
            "enabled": cfg.logging.enabled,
            "debug": cfg.logging.debug,
            "file": cfg.logging.file,
        },
        "metrics": {
            "enabled": cfg.metrics.enabled,
            "host": cfg.metrics.host,
            "port": cfg.metrics.port,
        },
        "workspace": cfg.workspace,
    }
