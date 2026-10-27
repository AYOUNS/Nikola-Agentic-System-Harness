"""Unit tests for agent.config."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent.config import ContextConfig, load_app_config, load_config


def test_load_config_valid(tmp_path: Path) -> None:
    """load_config returns a dict for a valid YAML file."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            llm:
              model: qwen-agentworld-35b-a3b
              base_url: https://example.com/
              temperature: 0.7
              timeout_seconds: 60
              available_models:
                - qwen-agentworld-35b-a3b
            loop:
              max_iterations: 30
              max_tokens: 8192
            workspace: /workspace
        """)
    )
    cfg = load_config(cfg_file)
    assert cfg["llm"]["model"] == "qwen-agentworld-35b-a3b"
    assert cfg["llm"]["base_url"] == "https://example.com/"
    assert cfg["workspace"] == "/workspace"


def test_load_config_missing_file(tmp_path: Path) -> None:
    """load_config raises FileNotFoundError for a missing file."""
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nonexistent.yaml")


def test_load_app_config_full_yaml(tmp_path: Path) -> None:
    """load_app_config returns typed AppConfig from complete YAML."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            llm:
              model: gemma4-31b-it
              embedding_model: octen-embedding-8b
              base_url: https://example.com/
              temperature: 0.5
              timeout_seconds: 120
            loop:
              max_iterations: 50
              max_tokens: 4096
            context:
              window_tokens: 262144
              reserve_output_tokens: 8192
              chars_per_token: 3.5
              max_observation_chars: 20000
              summary_max_tokens: 2000
              summary_reasoning_effort: low
            memory:
              db_path: /custom/memory.db
              qdrant:
                url: http://localhost:6333
                collection: test_memories
            workspace: /custom/workspace
        """)
    )
    cfg = load_app_config(cfg_file)
    
    # Verify LLM config
    assert cfg.llm.model == "gemma4-31b-it"
    assert cfg.llm.embedding_model == "octen-embedding-8b"
    assert cfg.llm.base_url == "https://example.com/"
    assert cfg.llm.temperature == 0.5
    assert cfg.llm.timeout_seconds == 120
    
    # Verify loop config
    assert cfg.loop.max_iterations == 50
    assert cfg.loop.max_tokens == 4096
    
    # Verify context config
    assert cfg.context.window_tokens == 262144
    assert cfg.context.reserve_output_tokens == 8192
    assert cfg.context.chars_per_token == 3.5
    assert cfg.context.max_observation_chars == 20000
    assert cfg.context.summary_max_tokens == 2000
    assert cfg.context.summary_reasoning_effort == "low"
    
    # Verify memory config
    assert cfg.memory.db_path == "/custom/memory.db"
    assert cfg.memory.qdrant_url == "http://localhost:6333"
    assert cfg.memory.qdrant_collection == "test_memories"
    
    # Verify workspace
    assert cfg.workspace == "/custom/workspace"


def test_load_app_config_defaults(tmp_path: Path) -> None:
    """load_app_config uses dataclass defaults for missing YAML values."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            llm:
              model: custom-model
        """)
    )
    cfg = load_app_config(cfg_file)
    
    # Custom value from YAML
    assert cfg.llm.model == "custom-model"
    
    # Defaults for missing values
    assert cfg.llm.temperature == 0.7
    assert cfg.loop.max_iterations == 100
    assert cfg.context.window_tokens == 262144
    assert cfg.workspace == "/workspace"


def test_load_app_config_empty_yaml(tmp_path: Path) -> None:
    """load_app_config uses all defaults for empty YAML."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("")
    
    cfg = load_app_config(cfg_file)
    
    # All values should be defaults
    assert cfg.llm.model == "qwen-agentworld-35b-a3b"
    assert cfg.loop.max_iterations == 100
    assert cfg.context.window_tokens == 262144
    assert cfg.workspace == "/workspace"


def test_context_config_compaction_budget() -> None:
    """ContextConfig.compaction_budget is correctly computed from window and reserve."""
    cfg = ContextConfig(
        window_tokens=262144,
        reserve_output_tokens=8192,
    )
    assert cfg.compaction_budget == 262144 - 8192
    assert cfg.compaction_budget == 253952


def test_context_config_frozen() -> None:
    """ContextConfig is immutable (frozen dataclass)."""
    cfg = ContextConfig()
    with pytest.raises(AttributeError):
        cfg.window_tokens = 1000  # type: ignore
