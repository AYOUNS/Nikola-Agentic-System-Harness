"""Rules loader for persistent user instructions.

Rules are markdown files in ${WORKSPACE}/.nikola/rules/*.md that accumulate
on top of the Agent Mode prompt. Unlike modes (which replace the system prompt),
rules persist across mode switches.

Example rule file (workspace/.nikola/rules/01-coding-style.md):
```markdown
---
description: Python coding style preferences
enabled: true
---

Always use type hints in function signatures.
Prefer dataclasses over plain classes when possible.
```
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Rule:
    """A single rule loaded from a markdown file.

    name: filename without extension (e.g., "01-coding-style")
    description: optional description from frontmatter
    enabled: whether this rule is active (default True)
    content: the rule text (markdown body)
    """

    name: str
    description: str
    enabled: bool
    content: str


def parse_rule_file(rule_file: Path) -> Rule:
    """Parse a single rule markdown file into a Rule dataclass."""
    content = rule_file.read_text(encoding="utf-8")

    # Parse frontmatter if present
    frontmatter: dict[str, Any] = {}
    body = content

    if content.startswith("---\n"):
        parts = content.split("---\n", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()
            except yaml.YAMLError as e:
                raise ValueError(
                    f"Invalid YAML frontmatter in {rule_file.name}: {e}"
                ) from e

    name = rule_file.stem
    description = frontmatter.get("description", "")
    enabled = frontmatter.get("enabled", True)

    return Rule(
        name=name,
        description=description,
        enabled=bool(enabled),
        content=body,
    )


def load_default_rules() -> list[Rule]:
    """Load built-in default rules bundled with the agent package (agent/rules/*.md)."""
    default_dir = Path(__file__).resolve().parent.parent / "rules"
    if not default_dir.exists() or not default_dir.is_dir():
        return []

    rule_files = sorted(default_dir.glob("*.md"))
    rules: list[Rule] = []
    for rule_file in rule_files:
        try:
            rule = parse_rule_file(rule_file)
            if rule.enabled:
                rules.append(rule)
        except Exception as e:
            logger.warning(f"Failed to load default rule from {rule_file.name}: {e}")
    return rules


def load_rules(workspace_root: str | Path, include_defaults: bool = True) -> list[Rule]:
    """Load all enabled rules from workspace (.nikola/rules/*.md) and package defaults.

    Workspace rules take precedence over default rules with the same name.
    Rules are sorted by name for deterministic order.

    Args:
        workspace_root: Path to workspace root (AGENT_WORKSPACE or config value)
        include_defaults: Whether to include built-in default rules from agent/rules/

    Returns:
        List of Rule objects, sorted by filename, enabled only

    Raises:
        ValueError: If a rule file has invalid frontmatter
        yaml.YAMLError: If frontmatter parsing fails
    """
    workspace_path = Path(workspace_root)
    rules_dir = workspace_path / ".nikola" / "rules"

    loaded_by_name: dict[str, Rule] = {}

    # Load default rules first if requested
    if include_defaults:
        for rule in load_default_rules():
            loaded_by_name[rule.name] = rule

    # Load workspace rules (overrides default rules if names match)
    if rules_dir.exists() and rules_dir.is_dir():
        rule_files = sorted(rules_dir.glob("*.md"))
        for rule_file in rule_files:
            try:
                rule = parse_rule_file(rule_file)
                if rule.enabled:
                    loaded_by_name[rule.name] = rule
                    logger.debug(f"Loaded workspace rule: {rule.name}")
                else:
                    # If explicitly disabled in workspace, remove any default matching rule
                    loaded_by_name.pop(rule.name, None)
                    logger.debug(f"Skipped disabled rule: {rule.name}")
            except Exception as e:
                raise ValueError(f"Failed to load rule from {rule_file.name}: {e}") from e

    rules = [loaded_by_name[name] for name in sorted(loaded_by_name.keys())]
    logger.info(f"Loaded {len(rules)} rule(s) (workspace: {rules_dir})")
    return rules


def format_rules_section(rules: list[Rule]) -> str:
    """Format rules into a section to append to the system prompt.

    Args:
        rules: List of Rule objects to format

    Returns:
        Formatted markdown section, or empty string if no rules
    """
    if not rules:
        return ""

    lines = ["\n---\n", "## User Rules\n"]

    for rule in rules:
        # Add rule header if it has a description
        if rule.description:
            lines.append(f"\n### {rule.name}: {rule.description}\n")
        else:
            lines.append(f"\n### {rule.name}\n")

        # Add rule content
        lines.append(rule.content)
        lines.append("\n")

    return "".join(lines)


def count_rules_tokens(rules: list[Rule], chars_per_token: float = 3.5) -> int:
    """Estimate token count for rules section.

    Args:
        rules: List of Rule objects
        chars_per_token: Character-to-token ratio

    Returns:
        Estimated token count
    """
    if not rules:
        return 0

    # Count characters in formatted section
    formatted = format_rules_section(rules)
    return int(len(formatted) / chars_per_token)
