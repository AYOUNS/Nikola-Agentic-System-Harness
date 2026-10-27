"""Unit tests for agent.context.rules (rules loader)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from agent.context.rules import (
    Rule,
    count_rules_tokens,
    format_rules_section,
    load_rules,
)


def test_load_rules_missing_directory(tmp_path: Path) -> None:
    """load_rules returns default rules when rules directory doesn't exist."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    rules = load_rules(workspace)
    assert len(rules) == 2
    names = {r.name for r in rules}
    assert "memory" in names
    assert "auto-memory" in names


def test_load_rules_empty_directory(tmp_path: Path) -> None:
    """load_rules returns default rules when rules directory is empty."""
    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)

    rules = load_rules(tmp_path)
    assert len(rules) == 2
    names = {r.name for r in rules}
    assert "memory" in names
    assert "auto-memory" in names


def test_load_rules_without_defaults(tmp_path: Path) -> None:
    """load_rules returns empty list when include_defaults is False."""
    rules = load_rules(tmp_path, include_defaults=False)
    assert rules == []


def test_load_rules_single_rule(tmp_path: Path) -> None:
    """load_rules loads a single rule file."""
    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)

    rule_content = dedent("""\
        ---
        description: Test rule
        enabled: true
        ---
        
        Always use type hints.
        """)

    (rules_dir / "01-test.md").write_text(rule_content)

    rules = load_rules(tmp_path, include_defaults=False)

    assert len(rules) == 1
    assert rules[0].name == "01-test"
    assert rules[0].description == "Test rule"
    assert rules[0].enabled is True
    assert "Always use type hints." in rules[0].content


def test_load_rules_sorted_by_filename(tmp_path: Path) -> None:
    """load_rules returns rules sorted by filename."""
    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)

    (rules_dir / "03-third.md").write_text("Third rule")
    (rules_dir / "01-first.md").write_text("First rule")
    (rules_dir / "02-second.md").write_text("Second rule")

    rules = load_rules(tmp_path, include_defaults=False)

    assert len(rules) == 3
    assert rules[0].name == "01-first"
    assert rules[1].name == "02-second"
    assert rules[2].name == "03-third"


def test_load_rules_skips_disabled(tmp_path: Path) -> None:
    """load_rules skips rules with enabled: false."""
    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)

    enabled_content = dedent("""\
        ---
        enabled: true
        ---
        Enabled rule
        """)

    disabled_content = dedent("""\
        ---
        enabled: false
        ---
        Disabled rule
        """)

    (rules_dir / "enabled.md").write_text(enabled_content)
    (rules_dir / "disabled.md").write_text(disabled_content)

    rules = load_rules(tmp_path, include_defaults=False)

    assert len(rules) == 1
    assert rules[0].name == "enabled"


def test_load_rules_no_frontmatter(tmp_path: Path) -> None:
    """load_rules handles rules without frontmatter."""
    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)

    (rules_dir / "simple.md").write_text("Just a simple rule with no frontmatter.")

    rules = load_rules(tmp_path, include_defaults=False)

    assert len(rules) == 1
    assert rules[0].name == "simple"
    assert rules[0].description == ""
    assert rules[0].enabled is True  # Default
    assert rules[0].content == "Just a simple rule with no frontmatter."


def test_load_rules_malformed_frontmatter(tmp_path: Path) -> None:
    """load_rules raises ValueError for malformed YAML frontmatter."""
    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)

    bad_content = dedent("""\
        ---
        invalid: yaml: syntax:
        ---
        Rule content
        """)

    (rules_dir / "bad.md").write_text(bad_content)

    with pytest.raises(ValueError, match="Invalid YAML frontmatter in bad.md"):
        load_rules(tmp_path)


def test_format_rules_section_empty() -> None:
    """format_rules_section returns empty string for no rules."""
    section = format_rules_section([])
    assert section == ""


def test_format_rules_section_single_rule() -> None:
    """format_rules_section formats a single rule."""
    rule = Rule(
        name="test-rule",
        description="Test description",
        enabled=True,
        content="Rule content here.",
    )

    section = format_rules_section([rule])

    assert "## User Rules" in section
    assert "### test-rule: Test description" in section
    assert "Rule content here." in section


def test_format_rules_section_no_description() -> None:
    """format_rules_section handles rules without description."""
    rule = Rule(
        name="no-desc",
        description="",
        enabled=True,
        content="Content without description.",
    )

    section = format_rules_section([rule])

    assert "### no-desc\n" in section
    assert "Content without description." in section


def test_format_rules_section_multiple_rules() -> None:
    """format_rules_section formats multiple rules."""
    rules = [
        Rule(name="rule1", description="First", enabled=True, content="First content"),
        Rule(name="rule2", description="Second", enabled=True, content="Second content"),
    ]

    section = format_rules_section(rules)

    assert "### rule1: First" in section
    assert "First content" in section
    assert "### rule2: Second" in section
    assert "Second content" in section


def test_count_rules_tokens_empty() -> None:
    """count_rules_tokens returns 0 for no rules."""
    tokens = count_rules_tokens([])
    assert tokens == 0


def test_count_rules_tokens_single_rule() -> None:
    """count_rules_tokens estimates tokens for a rule."""
    rule = Rule(
        name="test",
        description="Test",
        enabled=True,
        content="x" * 1000,  # 1000 chars
    )

    tokens = count_rules_tokens([rule], chars_per_token=4.0)

    # Should be roughly 1000 / 4.0 = 250 tokens (plus some overhead for headers)
    assert 200 < tokens < 300


def test_count_rules_tokens_multiple_rules() -> None:
    """count_rules_tokens sums tokens for multiple rules."""
    rules = [
        Rule(name="rule1", description="", enabled=True, content="x" * 500),
        Rule(name="rule2", description="", enabled=True, content="x" * 500),
    ]

    tokens = count_rules_tokens(rules, chars_per_token=4.0)

    # Should be roughly 1000 / 4.0 = 250 tokens (plus overhead)
    assert 200 < tokens < 350


def test_load_rules_ignores_non_md_files(tmp_path: Path) -> None:
    """load_rules only loads .md files."""
    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)

    (rules_dir / "rule.md").write_text("Valid rule")
    (rules_dir / "not-rule.txt").write_text("Not a rule")
    (rules_dir / "example.md.example").write_text("Example file")

    rules = load_rules(tmp_path, include_defaults=False)

    assert len(rules) == 1
    assert rules[0].name == "rule"


def test_rules_survive_clear(tmp_path: Path) -> None:
    """Rules are re-applied after session.reset_history()."""
    from unittest.mock import Mock

    from agent.cli.session import Session, system_message
    from agent.cli.ui import ConsoleUI
    from agent.tools import ToolRegistry

    # Create a rule
    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "test.md").write_text("Test rule content")

    # Create session with workspace
    session = Session(
        messages=[system_message("default", str(tmp_path))],
        mode="default",
        model="test",
        tools=ToolRegistry(),
        ui=Mock(spec=ConsoleUI),
        workspace_root=str(tmp_path),
    )

    # Check rule is in initial system message
    initial_content = session.messages[0]["content"]
    assert "Test rule content" in initial_content

    # Add some conversation
    from openai.types.chat import ChatCompletionUserMessageParam
    session.messages.append(ChatCompletionUserMessageParam(role="user", content="Hi"))

    # Reset history
    session.reset_history()

    # Rule should still be there
    reset_content = session.messages[0]["content"]
    assert "Test rule content" in reset_content
    assert len(session.messages) == 1  # Only system message


def test_rules_survive_mode_switch(tmp_path: Path) -> None:
    """Rules are re-applied after session.switch_mode()."""
    from unittest.mock import Mock

    from agent.cli.session import Session, system_message
    from agent.cli.ui import ConsoleUI
    from agent.tools import ToolRegistry

    # Create a rule
    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "test.md").write_text("Persistent rule")

    # Create session
    session = Session(
        messages=[system_message("default", str(tmp_path))],
        mode="default",
        model="test",
        tools=ToolRegistry(),
        ui=Mock(spec=ConsoleUI),
        workspace_root=str(tmp_path),
    )

    # Check rule is present
    assert "Persistent rule" in session.messages[0]["content"]

    # Switch mode (default is the only one guaranteed to exist)
    session.switch_mode("default")

    # Rule should still be there after mode switch
    assert "Persistent rule" in session.messages[0]["content"]
def test_load_rules_workspace_overrides_default(tmp_path: Path) -> None:
    """Workspace rules override default rules with the same name."""
    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)

    override_content = dedent("""\
        ---
        description: Custom memory rule
        enabled: true
        ---
        Custom memory instructions.
        """)
    (rules_dir / "memory.md").write_text(override_content)

    rules = load_rules(tmp_path, include_defaults=True)
    memory_rule = next((r for r in rules if r.name == "memory"), None)
    assert memory_rule is not None
    assert memory_rule.description == "Custom memory rule"
    assert "Custom memory instructions." in memory_rule.content


def test_load_rules_workspace_disables_default(tmp_path: Path) -> None:
    """Explicitly disabling a default rule in workspace removes it."""
    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)

    disabled_content = dedent("""\
        ---
        enabled: false
        ---
        Disabled
        """)
    (rules_dir / "memory.md").write_text(disabled_content)

    rules = load_rules(tmp_path, include_defaults=True)
    names = {r.name for r in rules}
    assert "memory" not in names
    assert "auto-memory" in names
