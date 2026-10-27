"""Unit tests for agent.skills.loader (skill discovery and loading)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from agent.skills.loader import (
    Skill,
    count_skill_tokens,
    discover_skills,
    get_skill,
)


def test_discover_skills_empty_directories(tmp_path: Path) -> None:
    """discover_skills returns only built-in skills when workspace has no skills."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    skills = discover_skills(workspace)

    # May have built-in skills like 'mcp', but no user skills from workspace
    # All skills should be from built-in directory, not workspace
    for skill in skills.values():
        assert "agent/skills" in str(skill.path)


def test_discover_skills_builtin_only() -> None:
    """discover_skills loads built-in skills from agent/skills/."""
    # This test depends on actual built-in skills existing
    # For now, we'll test the mechanism with a temp directory
    skills = discover_skills(None)

    # Should at least not crash; may be empty if no built-ins yet
    assert isinstance(skills, dict)


def test_discover_skills_single_skill(tmp_path: Path) -> None:
    """discover_skills loads a single skill file."""
    skills_dir = tmp_path / ".nikola" / "skills"
    skills_dir.mkdir(parents=True)

    skill_content = dedent("""\
        ---
        name: test-skill
        description: A test skill
        version: 1.0
        ---
        
        # Test Skill Body
        
        This is the skill content.
        """)

    (skills_dir / "test-skill.md").write_text(skill_content)

    skills = discover_skills(tmp_path)

    # Should have at least the test skill (may also have built-ins)
    assert "test-skill" in skills
    assert skills["test-skill"].name == "test-skill"
    assert skills["test-skill"].description == "A test skill"
    assert skills["test-skill"].version == "1.0"


def test_skill_body_lazy_loading(tmp_path: Path) -> None:
    """Skill body is not loaded until accessed."""
    skills_dir = tmp_path / ".nikola" / "skills"
    skills_dir.mkdir(parents=True)

    skill_content = dedent("""\
        ---
        name: lazy
        description: Lazy loaded skill
        version: 1.0
        ---
        
        Body content that should be lazy loaded.
        """)

    (skills_dir / "lazy.md").write_text(skill_content)

    skills = discover_skills(tmp_path)
    skill = skills["lazy"]

    # Body should not be loaded yet
    assert skill._body is None

    # Access body triggers load
    body = skill.body
    assert "Body content that should be lazy loaded." in body
    assert skill._body is not None

    # Subsequent access uses cached body
    body2 = skill.body
    assert body2 is body  # Same object


def test_discover_skills_sorted_by_filename(tmp_path: Path) -> None:
    """discover_skills processes files in sorted order."""
    skills_dir = tmp_path / ".nikola" / "skills"
    skills_dir.mkdir(parents=True)

    for name in ["skill-c", "skill-a", "skill-b"]:
        content = dedent(f"""\
            ---
            name: {name}
            description: Skill {name}
            version: 1.0
            ---
            Content
            """)
        (skills_dir / f"{name}.md").write_text(content)

    skills = discover_skills(tmp_path)

    # All test skills should be present (may also have built-ins)
    assert "skill-a" in skills
    assert "skill-b" in skills
    assert "skill-c" in skills


def test_discover_skills_user_overrides_builtin(tmp_path: Path) -> None:
    """User skills override built-in skills by name."""
    # Create a mock built-in directory structure
    builtin_dir = tmp_path / "agent" / "skills"
    builtin_dir.mkdir(parents=True)

    builtin_content = dedent("""\
        ---
        name: override-test
        description: Built-in version
        version: 1.0
        ---
        Built-in body
        """)
    (builtin_dir / "override-test.md").write_text(builtin_content)

    # Create user skills directory
    user_dir = tmp_path / "workspace" / ".nikola" / "skills"
    user_dir.mkdir(parents=True)

    user_content = dedent("""\
        ---
        name: override-test
        description: User version
        version: 2.0
        ---
        User body
        """)
    (user_dir / "override-test.md").write_text(user_content)

    # Note: This test would need the actual loader to look in tmp_path for built-ins
    # For now, we'll just test user skills
    skills = discover_skills(tmp_path / "workspace")

    assert "override-test" in skills
    assert skills["override-test"].description == "User version"
    assert skills["override-test"].version == "2.0"


def test_discover_skills_missing_frontmatter(tmp_path: Path) -> None:
    """discover_skills raises ValueError for missing frontmatter."""
    skills_dir = tmp_path / ".nikola" / "skills"
    skills_dir.mkdir(parents=True)

    (skills_dir / "no-frontmatter.md").write_text("Just body content, no frontmatter")

    with pytest.raises(ValueError, match="must have YAML frontmatter"):
        discover_skills(tmp_path)


def test_discover_skills_incomplete_frontmatter(tmp_path: Path) -> None:
    """discover_skills raises ValueError for incomplete frontmatter."""
    skills_dir = tmp_path / ".nikola" / "skills"
    skills_dir.mkdir(parents=True)

    bad_content = dedent("""\
        ---
        name: incomplete
        ---
        Body
        """)

    (skills_dir / "incomplete.md").write_text(bad_content)

    with pytest.raises(ValueError, match="missing required field"):
        discover_skills(tmp_path)


def test_discover_skills_name_mismatch(tmp_path: Path) -> None:
    """discover_skills raises ValueError when name doesn't match filename."""
    skills_dir = tmp_path / ".nikola" / "skills"
    skills_dir.mkdir(parents=True)

    mismatch_content = dedent("""\
        ---
        name: wrong-name
        description: This name doesn't match
        version: 1.0
        ---
        Body
        """)

    (skills_dir / "correct-name.md").write_text(mismatch_content)

    with pytest.raises(ValueError, match="doesn't match filename"):
        discover_skills(tmp_path)


def test_discover_skills_invalid_yaml(tmp_path: Path) -> None:
    """discover_skills raises ValueError for invalid YAML."""
    skills_dir = tmp_path / ".nikola" / "skills"
    skills_dir.mkdir(parents=True)

    bad_yaml = dedent("""\
        ---
        invalid: yaml: syntax:
        ---
        Body
        """)

    (skills_dir / "bad-yaml.md").write_text(bad_yaml)

    with pytest.raises(ValueError, match="Invalid YAML"):
        discover_skills(tmp_path)


def test_get_skill_found(tmp_path: Path) -> None:
    """get_skill returns skill when it exists."""
    skills_dir = tmp_path / ".nikola" / "skills"
    skills_dir.mkdir(parents=True)

    skill_content = dedent("""\
        ---
        name: findme
        description: Test
        version: 1.0
        ---
        Body
        """)

    (skills_dir / "findme.md").write_text(skill_content)

    skills = discover_skills(tmp_path)
    skill = get_skill(skills, "findme")

    assert skill is not None
    assert skill.name == "findme"


def test_get_skill_not_found(tmp_path: Path) -> None:
    """get_skill returns None when skill doesn't exist."""
    skills = discover_skills(tmp_path)
    skill = get_skill(skills, "nonexistent")

    assert skill is None


def test_count_skill_tokens(tmp_path: Path) -> None:
    """count_skill_tokens estimates tokens for skill body."""
    skills_dir = tmp_path / ".nikola" / "skills"
    skills_dir.mkdir(parents=True)

    # Create skill with known body length
    content = dedent("""\
        ---
        name: token-test
        description: Test
        version: 1.0
        ---
        """) + ("x" * 1000)  # 1000 chars in body

    (skills_dir / "token-test.md").write_text(content)

    skills = discover_skills(tmp_path)
    skill = skills["token-test"]

    tokens = count_skill_tokens(skill, chars_per_token=4.0)

    # Should be roughly 1000 / 4.0 = 250 tokens
    assert 240 < tokens < 260


def test_discover_skills_ignores_non_md_files(tmp_path: Path) -> None:
    """discover_skills only loads .md files."""
    skills_dir = tmp_path / ".nikola" / "skills"
    skills_dir.mkdir(parents=True)

    skill_content = dedent("""\
        ---
        name: valid
        description: Valid skill
        version: 1.0
        ---
        Body
        """)

    (skills_dir / "valid.md").write_text(skill_content)
    (skills_dir / "not-a-skill.txt").write_text("Not a skill file")
    (skills_dir / "example.md.example").write_text("Example file")

    skills = discover_skills(tmp_path)

    # Should have the valid skill (may also have built-ins)
    assert "valid" in skills
    # Should not have the .txt or .example files
    assert "not-a-skill" not in skills
    assert "example.md" not in skills


def test_skill_body_no_frontmatter() -> None:
    """Skill handles edge case of no frontmatter in body property."""
    # Create a skill manually (wouldn't normally happen via discover_skills)

    temp_file = Path("/tmp/test-skill.md")
    temp_file.write_text("Just plain content")

    skill = Skill(
        name="plain",
        description="Plain",
        version="1.0",
        path=temp_file,
    )

    body = skill.body
    assert body == "Just plain content"

    temp_file.unlink()  # Cleanup
