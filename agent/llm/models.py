"""Model information and context window detection.

Queries the LLM API's /v1/models endpoint to get model capabilities,
particularly the context window size. Falls back to conservative defaults
if the API doesn't provide the information.
"""

from __future__ import annotations

import logging
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)


def get_model_context_window(client: OpenAI, model_name: str) -> int:
    """Get the context window size for a model from the API.

    Queries the /v1/models endpoint to get the model's context_length.
    Falls back to a conservative 8192 tokens if:
    - The API call fails
    - The model is not found
    - The context_length field is missing

    Args:
        client: OpenAI client instance
        model_name: Name of the model (e.g., "qwen-agentworld-35b-a3b")

    Returns:
        Context window size in tokens (default: 8192)
    """
    try:
        # Query available models
        models = client.models.list()
        
        # Find the requested model
        for model in models.data:
            if model.id == model_name:
                # Try to get context_length (OpenAI standard field)
                if hasattr(model, "context_length"):
                    context_length = model.context_length
                    logger.info(
                        f"Model '{model_name}' context window: {context_length:,} tokens"
                    )
                    return context_length
                
                # Some APIs use max_model_len instead
                if hasattr(model, "max_model_len"):
                    max_len = model.max_model_len
                    logger.info(
                        f"Model '{model_name}' context window: {max_len:,} tokens"
                    )
                    return max_len
                
                # Model found but no context info. The InnKube gateway is one of these:
                # it returns only the bare OpenAI fields (id, object, created, owned_by),
                # so detection cannot succeed there and the window has to be configured.
                logger.warning(
                    f"Model '{model_name}' found but no context_length field. "
                    f"Using default 8192 tokens — set context.window_tokens in config.yaml "
                    f"to the model's real window."
                )
                return 8192
        
        # Model not found in list
        logger.warning(
            f"Model '{model_name}' not found in /v1/models response. "
            f"Using default 8192 tokens."
        )
        return 8192
        
    except Exception as e:
        # API call failed
        logger.warning(
            f"Failed to query /v1/models endpoint: {e}. "
            f"Using default 8192 tokens."
        )
        return 8192


def get_context_config(client: OpenAI, config: dict[str, Any]) -> dict[str, Any]:
    """Get context configuration with dynamic window size from model info.

    An explicit context.window_tokens in config.yaml wins and skips the API call: a
    gateway that does not advertise a window (InnKube, and LiteLLM proxies generally)
    cannot be detected from, so a configured value is the only way to run at the model's
    real size, and querying anyway would only re-log the warning every start.

    Args:
        client: OpenAI client instance
        config: Application configuration dict

    Returns:
        Context configuration dict with:
            - window_tokens: from config, else from API, else default 8192
            - reserve_output_tokens: from config or default 2048
            - chars_per_token: from config or default 3.5
            - max_observation_chars: from config or default 10000
    """
    model_name = config.get("llm", {}).get("model", "qwen-agentworld-35b-a3b")
    context_config = config.get("context", {})

    configured_window = context_config.get("window_tokens")
    if configured_window:
        logger.info(
            f"Model '{model_name}' context window: {configured_window:,} tokens (config.yaml)"
        )
        window_tokens = configured_window
    else:
        window_tokens = get_model_context_window(client, model_name)

    # Get other settings from config with defaults
    return {
        "window_tokens": window_tokens,
        "reserve_output_tokens": context_config.get("reserve_output_tokens", 2048),
        "chars_per_token": context_config.get("chars_per_token", 3.5),
        "max_observation_chars": context_config.get("max_observation_chars", 10000),
        "summary_max_tokens": context_config.get("summary_max_tokens", 1500),
        "summary_reasoning_effort": context_config.get("summary_reasoning_effort", "none"),
    }
