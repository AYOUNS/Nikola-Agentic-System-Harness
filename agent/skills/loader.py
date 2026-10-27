"""Skills loader and registry for just-in-time instruction disclosure.

Skills are markdown files with frontmatter that contain specialized instructions
loaded on demand. They live in:
- agent/skills/ (built-in skills)
- ${WORKSPACE}/.nikola/skills/ (user skills, override built-ins by name)

Example skill file (agent/skills/mcp.md):
```markdown
---
name: mcp
description: Guide to using MCP server tools
version: 1.0
---

# MCP Integration

MCP tools follow the naming convention: mcp_<server>__<tool>
...
```
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """A skill bundle with lazy-loaded body.

    name: skill name (must match filename without extension)
    description: short description for listing
    version: version string (e.g., "1.0")
    path: path to the skill file
    _body: cached body content (None until loaded)
    """

    name: str
    description: str
    version: str
    path: Path
    _body: str | None = field(default=None, repr=False)

    @property
    def body(self) -> str:
        """Get the skill body, loading it from disk if not already cached.

        Returns:
            The markdown body content (everything after frontmatter)
        """
        if self._body is None:
            content = self.path.read_text(encoding="utf-8")

            # Skip frontmatter to get body
            if content.startswith("---\n"):
                parts = content.split("---\n", 2)
                if len(parts) >= 3:
                    self._body = parts[2].strip()
                else:
                    self._body = ""
            else:
                # No frontmatter, entire file is body
                self._body = content.strip()

            logger.debug(f"Loaded skill body: {self.name} ({len(self._body)} chars)")

        return self._body


def _load_skill_from_file(path: Path) -> Skill:
    """Load a single skill from a markdown file.

    Args:
        path: Path to .md skill file

    Returns:
        Skill object with metadata (body not yet loaded)

    Raises:
        ValueError: If frontmatter is missing, invalid, or name doesn't match filename
    """
    content = path.read_text(encoding="utf-8")

    # Parse frontmatter (required for skills)
    if not content.startswith("---\n"):
        raise ValueError(f"Skill {path.name} must have YAML frontmatter")

    parts = content.split("---\n", 2)
    if len(parts) < 3:
        raise ValueError(f"Skill {path.name} has incomplete frontmatter")

    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {path.name}: {e}") from e

    if not isinstance(frontmatter, dict):
        raise TypeError(f"Frontmatter in {path.name} must be a dictionary")

    # Extract required fields
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    version = frontmatter.get("version")

    if not name:
        raise ValueError(f"Skill {path.name} missing required field 'name'")
    if not description:
        raise ValueError(f"Skill {path.name} missing required field 'description'")
    if not version:
        raise ValueError(f"Skill {path.name} missing required field 'version'")

    # Validate name matches filename or parent directory (for SKILL.md files)
    if path.name == "SKILL.md":
        expected_name = path.parent.name
    else:
        expected_name = path.stem
    
    if name != expected_name:
        raise ValueError(
            f"Skill name '{name}' doesn't match filename '{path.name}' (expected '{expected_name}')"
        )

    return Skill(
        name=str(name),
        description=str(description),
        version=str(version),
        path=path,
    )


def discover_skills(workspace_root: str | Path | None = None) -> dict[str, Skill]:
    """Discover all skills from built-in and workspace directories.

    Skills are loaded from two locations in precedence order:
    1. ${WORKSPACE}/.nikola/skills/*.md and */SKILL.md (user skills, highest priority)
    2. agent/skills/*.md (built-in skills)

    User skills override built-ins by name.

    Args:
        workspace_root: Optional workspace root for user skills

    Returns:
        Dictionary mapping skill name to Skill object (bodies not yet loaded)

    Raises:
        ValueError: If any skill file is malformed or has name mismatch
    """
    skills: dict[str, Skill] = {}

    # Load built-in skills first
    builtin_dir = Path(__file__).parent.parent / "skills"
    if builtin_dir.exists() and builtin_dir.is_dir():
        for skill_file in sorted(builtin_dir.glob("*.md")):
            try:
                skill = _load_skill_from_file(skill_file)
                skills[skill.name] = skill
                logger.debug(f"Discovered built-in skill: {skill.name}")
            except ValueError as e:
                logger.error(f"Failed to load built-in skill {skill_file.name}: {e}")
                raise

    # Load workspace skills (these override built-ins)
    if workspace_root:
        workspace_path = Path(workspace_root)
        user_skills_dir = workspace_path / ".nikola" / "skills"

        if user_skills_dir.exists():
            if not user_skills_dir.is_dir():
                logger.warning(f"Skills path exists but is not a directory: {user_skills_dir}")
            else:
                # Find skills in two patterns:
                # 1. *.md files directly in .nikola/skills/
                # 2. */SKILL.md files in subdirectories
                skill_files = []
                skill_files.extend(user_skills_dir.glob("*.md"))
                skill_files.extend(user_skills_dir.glob("*/SKILL.md"))
                
                for skill_file in sorted(skill_files):
                    try:
                        skill = _load_skill_from_file(skill_file)
                        if skill.name in skills:
                            logger.info(f"User skill '{skill.name}' overrides built-in")
                        skills[skill.name] = skill
                        logger.debug(f"Discovered user skill: {skill.name}")
                    except ValueError as e:
                        logger.error(f"Failed to load user skill {skill_file.name}: {e}")
                        raise

    logger.info(f"Discovered {len(skills)} skill(s)")
    return skills


def get_skill(skills: dict[str, Skill], name: str) -> Skill | None:
    """Get a skill by name.

    Args:
        skills: Skills dictionary from discover_skills()
        name: Skill name

    Returns:
        Skill object if found, None otherwise
    """
    return skills.get(name)


def count_skill_tokens(skill: Skill, chars_per_token: float = 3.5) -> int:
    """Estimate token count for a loaded skill body.

    Args:
        skill: Skill object (body will be loaded if not already)
        chars_per_token: Character-to-token ratio

    Returns:
        Estimated token count
    """
    body = skill.body  # Triggers lazy load if needed
    return int(len(body) / chars_per_token)
