"""Layer 1 — raw OpenAI client factory.

Only file in the project that touches os.environ for LLM credentials
and imports openai.OpenAI directly.
"""

from __future__ import annotations

import os
from typing import Any

from openai import OpenAI


def make_client(config: dict[str, Any]) -> OpenAI:
    """make_client creates an OpenAI-compatible client pointed at InnKube.

    Reads the API key from INNKUBE_ACCESS_TOKEN and the base URL from
    config["llm"]["base_url"]. If OPENAI_BASE_URL is set in the environment
    it overrides the config value, allowing per-deployment URL overrides
    without modifying config.yaml.

    config: parsed config dict from load_config(). Must contain llm.base_url.

    Returns a configured openai.OpenAI instance ready for use in the
    dispatcher. The instance is not connected until the first API call.

    Raises EnvironmentError if INNKUBE_ACCESS_TOKEN is not set.

    Example:
        cfg    = load_config()
        client = make_client(cfg)

    See also: make_chat_completions_effect, make_chat_completions_stream_effect.
    """
    token = os.environ.get("INNKUBE_ACCESS_TOKEN")
    if not token:
        raise OSError(
            "INNKUBE_ACCESS_TOKEN environment variable is not set. "
            "Copy .env.example to .env and fill in your token."
        )

    # OPENAI_BASE_URL overrides config.yaml (12-factor pattern).
    base_url = os.environ.get("OPENAI_BASE_URL") or config["llm"]["base_url"]

    return OpenAI(api_key=token, base_url=base_url)
