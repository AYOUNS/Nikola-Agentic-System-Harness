#!/usr/bin/env python3
"""Quick test of LoggingHook without running full CLI.

Usage:
    python scripts/test_logging_hook.py
    python scripts/test_logging_hook.py --debug
"""

import argparse
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.config import load_app_config
from agent.hooks import get_hook_registry
from agent.hooks.types import (
    LLMCallEvent,
    SubAgentEndEvent,
    SubAgentStartEvent,
    ToolCallEvent,
)
from agent.logging import configure_logging
from agent.observability import LoggingHook


def main():
    """Test the logging hook with sample events."""
    parser = argparse.ArgumentParser(description="Test LoggingHook")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()
    
    print("=" * 70)
    print("LoggingHook Test")
    print("=" * 70)
    
    # Load config
    try:
        cfg = load_app_config()
        print(f"\n✅ Config loaded:")
        print(f"   - logging.enabled: {cfg.logging.enabled}")
        print(f"   - logging.debug: {cfg.logging.debug}")
    except FileNotFoundError:
        print("\n⚠️  config.yaml not found, using defaults")
        from agent.config import AppConfig, LoopConfig, ContextConfig, LLMConfig, MemoryConfig, LoggingConfig
        cfg = AppConfig(
            loop=LoopConfig(),
            context=ContextConfig(),
            llm=LLMConfig(),
            memory=MemoryConfig(),
            logging=LoggingConfig(enabled=True, debug=args.debug),
            workspace="/workspace"
        )
    
    # Override debug mode if requested
    if args.debug:
        from agent.config import LoggingConfig, replace
        cfg = replace(cfg, logging=LoggingConfig(enabled=True, debug=True))
        print(f"   - Debug mode: ENABLED (via --debug flag)")
    
    # Configure logging to stderr for visibility
    configure_logging(
        level=logging.DEBUG if args.debug else logging.INFO,
        session_id="test-session-001",
    )
    
    # Initialize hook
    registry = get_hook_registry()
    hook = LoggingHook(
        enabled=cfg.logging.enabled,
        debug=cfg.logging.debug,
    )
    registry.register(hook)
    
    print(f"\n✅ LoggingHook registered")
    print(f"   - Enabled: {cfg.logging.enabled}")
    print(f"   - Debug: {cfg.logging.debug}")
    print(f"   - Hooks registered: {len(registry._hooks)}")
    
    print("\n" + "=" * 70)
    print("Emitting Test Events (watch JSON output on stderr)")
    print("=" * 70 + "\n")
    
    # Test 1: Sub-agent start
    print("1️⃣  Emitting SubAgentStartEvent...")
    registry.emit_subagent_start(
        SubAgentStartEvent(
            parent_session_id="test-session-001",
            subagent_id="researcher-alpha",
            role_name="file-scout",
            task="Search for Python files matching the pattern '*.py' in the workspace",
        )
    )
    
    # Test 2: Tool call
    print("2️⃣  Emitting ToolCallEvent...")
    registry.emit_tool_call(
        ToolCallEvent(
            session_id="test-session-001",
            tool_name="read_file",
            arguments={"path": "agent/config.py"},
            result="Successfully read file (250 lines)",
            permission_outcome="allow",
            duration_ms=42.5,
        )
    )
    
    # Test 3: LLM call (success)
    print("3️⃣  Emitting LLMCallEvent (success)...")
    registry.emit_llm_call(
        LLMCallEvent(
            session_id="test-session-001",
            model="qwen-agentworld-35b-a3b",
            prompt_tokens=250,
            completion_tokens=150,
            total_tokens=400,
            duration_ms=1500,
            success=True,
        )
    )
    
    # Test 4: Sub-agent end
    print("4️⃣  Emitting SubAgentEndEvent...")
    registry.emit_subagent_end(
        SubAgentEndEvent(
            parent_session_id="test-session-001",
            subagent_id="researcher-alpha",
            role_name="file-scout",
            success=True,
            iterations=5,
            tools_used=["read_file", "list_files", "grep"],
            duration_ms=3500,
            result="Found 42 Python files matching the pattern",
        )
    )
    
    # Test 5: LLM call (failure)
    print("5️⃣  Emitting LLMCallEvent (failure)...")
    registry.emit_llm_call(
        LLMCallEvent(
            session_id="test-session-001",
            model="qwen-agentworld-35b-a3b",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            duration_ms=100,
            success=False,
        )
    )
    
    print("\n" + "=" * 70)
    print("✅ All events emitted successfully!")
    print("=" * 70)
    
    if args.debug:
        print("\n💡 Debug mode shows:")
        print("   - → ENTRY markers (when methods start)")
        print("   - ← EXIT markers (when methods complete)")
        print("   - Additional metrics (tokens/sec, cost, efficiency)")
    else:
        print("\n💡 Try again with --debug to see detailed entry/exit logging:")
        print("   python scripts/test_logging_hook.py --debug")
    
    print("\n💡 Query the JSON output:")
    print('   python scripts/test_logging_hook.py 2>&1 | jq \'select(.event_type)\'')
    print()


if __name__ == "__main__":
    main()
