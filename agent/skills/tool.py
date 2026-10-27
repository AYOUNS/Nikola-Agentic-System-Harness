"""Skill loading tool for just-in-time instruction disclosure.

Implements the load_skill tool that allows the model to load skill bodies
on demand rather than keeping all skills in context at all times.
"""

from __future__ import annotations

import logging

from openai.types.chat import ChatCompletionToolParam

from agent.skills.loader import Skill, discover_skills, get_skill

logger = logging.getLogger(__name__)

# Global skills registry (populated on first use)
_skills_cache: dict[str, Skill] | None = None


def _get_skills_registry(workspace_root: str | None = None) -> dict[str, Skill]:
    """Get or initialize the skills registry.

    Args:
        workspace_root: Workspace root for user skills discovery

    Returns:
        Dictionary of discovered skills
    """
    global _skills_cache
    if _skills_cache is None:
        _skills_cache = discover_skills(workspace_root)
    return _skills_cache


def make_load_skill_schema(workspace_root: str | None = None) -> ChatCompletionToolParam:
    """Generate the load_skill tool schema with current available skills.

    The schema dynamically lists all discoverable skills in the enum,
    so the model knows what skills are available.

    Args:
        workspace_root: Workspace root for skill discovery

    Returns:
        Tool schema for load_skill
    """
    skills = _get_skills_registry(workspace_root)

    # Build enum of skill names with descriptions in the enum item descriptions
    skill_enum = list(skills.keys())
    
    # Create description that lists all skills
    skill_list = "\n".join(
        f"  - {name}: {skill.description}"
        for name, skill in sorted(skills.items())
    )

    description = f"""Load a skill to get specialized instructions for a specific task.

Available skills:
{skill_list if skill_list else "  (none available)"}

Skills contain detailed guidance on specific topics. Load a skill when you need
specialized instructions that aren't in the base prompt."""

    return {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the skill to load",
                        "enum": skill_enum if skill_enum else ["(no skills available)"],
                    }
                },
                "required": ["name"],
            },
        },
    }


def load_skill_tool(
    name: str,
    loaded_skills: set[str],
    workspace_root: str | None = None,
) -> str:
    """Load a skill and return its body as an observation.

    Args:
        name: Skill name to load
        loaded_skills: Set of already-loaded skill names (mutated in place)
        workspace_root: Workspace root for skill discovery

    Returns:
        Skill body if found and not already loaded, or error/note message
    """
    skills = _get_skills_registry(workspace_root)

    # Check if skill exists
    skill = get_skill(skills, name)
    if skill is None:
        available = ", ".join(sorted(skills.keys()))
        return f"Error: Unknown skill '{name}'. Available skills: {available}"

    # Check if already loaded
    if name in loaded_skills:
        return f"[Note: Skill '{name}' was already loaded earlier in this conversation]"

    # Load the skill body
    body = skill.body
    loaded_skills.add(name)

    logger.info(f"Loaded skill '{name}' ({len(body)} chars)")

    # Return with header for clarity
    return f"# Skill: {name}\n\n{body}"


def get_loaded_skills_info(
    loaded_skills: set[str],
    workspace_root: str | None = None,
    chars_per_token: float = 3.5,
) -> list[tuple[str, int]]:
    """Get information about loaded skills for /context display.

    Args:
        loaded_skills: Set of loaded skill names
        workspace_root: Workspace root for skill discovery
        chars_per_token: Ratio for token estimation

    Returns:
        List of (skill_name, token_count) tuples
    """
    if not loaded_skills:
        return []

    skills = _get_skills_registry(workspace_root)
    info = []

    for name in sorted(loaded_skills):
        skill = get_skill(skills, name)
        if skill:
            # Estimate tokens (body already loaded since skill was loaded)
            body_len = len(skill._body) if skill._body else 0
            tokens = int(body_len / chars_per_token)
            info.append((name, tokens))

    return info
