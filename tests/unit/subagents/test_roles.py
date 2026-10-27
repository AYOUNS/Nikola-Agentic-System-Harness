"""Unit tests for sub-agent roles and configurations.

Tests role definitions and their allowlists.
"""

import pytest

from agent.subagents.roles import (
    BUILTIN_ROLES,
    SubAgentRole,
    get_role,
    list_roles,
    make_file_scout_role,
    make_scheduler_role,
)


class TestSubAgentRole:
    """Test SubAgentRole dataclass."""
    
    def test_role_has_required_fields(self):
        """SubAgentRole has all required fields."""
        role = SubAgentRole(
            name="test-role",
            description="Test description",
            allowlist=["read_file"],
        )
        
        assert role.name == "test-role"
        assert role.description == "Test description"
        assert role.allowlist == ["read_file"]
        assert role.max_iterations == 8
        assert role.max_result_chars == 2000
    
    def test_role_custom_limits(self):
        """SubAgentRole can override default limits."""
        role = SubAgentRole(
            name="custom-role",
            description="Custom limits",
            allowlist=["read_file"],
            max_iterations=20,
            max_result_chars=5000,
        )
        
        assert role.max_iterations == 20
        assert role.max_result_chars == 5000


class TestFileScoutRole:
    """Test file-scout role configuration."""
    
    def test_file_scout_role_name(self):
        """file-scout has correct name."""
        role = make_file_scout_role()
        assert role.name == "file-scout"
    
    def test_file_scout_has_description(self):
        """file-scout has non-empty description."""
        role = make_file_scout_role()
        assert len(role.description) > 0
        assert "read-only" in role.description.lower()
    
    def test_file_scout_limits(self):
        """file-scout has correct limits."""
        role = make_file_scout_role()
        assert role.max_iterations == 8
        assert role.max_result_chars == 2000
    
    def test_file_scout_read_tools_in_allowlist(self):
        """file-scout allowlist contains read-only tools."""
        role = make_file_scout_role()
        
        # Read tools should be in allowlist
        assert "read_file" in role.allowlist
        assert "list_files" in role.allowlist
        assert "search_text" in role.allowlist
        assert "glob" in role.allowlist
        assert "grep" in role.allowlist
    
    def test_file_scout_write_tools_not_in_allowlist(self):
        """file-scout allowlist excludes write tools."""
        role = make_file_scout_role()
        
        # Write tools should NOT be in allowlist
        assert "write_file" not in role.allowlist
        assert "modify_file" not in role.allowlist
        assert "create_folder" not in role.allowlist
        assert "remember" not in role.allowlist
    
    def test_file_scout_dangerous_tools_not_in_allowlist(self):
        """file-scout allowlist excludes dangerous tools."""
        role = make_file_scout_role()
        
        assert "execute_command" not in role.allowlist
        assert "delegate" not in role.allowlist


class TestSchedulerRole:
    """Test scheduler role configuration."""
    
    def test_scheduler_role_name(self):
        """scheduler has correct name."""
        role = make_scheduler_role()
        assert role.name == "scheduler"
    
    def test_scheduler_has_description(self):
        """scheduler has non-empty description."""
        role = make_scheduler_role()
        assert len(role.description) > 0
        assert "calendar" in role.description.lower()
    
    def test_scheduler_limits(self):
        """scheduler has appropriate limits."""
        role = make_scheduler_role()
        assert role.max_iterations == 10
        assert role.max_result_chars == 1500
    
    def test_scheduler_calendar_tools_in_allowlist(self):
        """scheduler allowlist contains MCP calendar tools."""
        role = make_scheduler_role()
        
        # Calendar tools should be in allowlist (with mcp_ prefix)
        assert "mcp_calendar__current_datetime" in role.allowlist
        assert "mcp_calendar__list_events" in role.allowlist
        assert "mcp_calendar__find_free_slots" in role.allowlist
        assert "mcp_calendar__create_event" in role.allowlist
        assert "mcp_calendar__update_event" in role.allowlist
    
    def test_scheduler_read_tools_in_allowlist(self):
        """scheduler allowlist contains basic read tools."""
        role = make_scheduler_role()
        
        assert "read_file" in role.allowlist
        assert "list_files" in role.allowlist
    
    def test_scheduler_write_tools_not_in_allowlist(self):
        """scheduler allowlist excludes file write tools."""
        role = make_scheduler_role()
        
        assert "write_file" not in role.allowlist
    
    def test_scheduler_dangerous_tools_not_in_allowlist(self):
        """scheduler allowlist excludes dangerous tools."""
        role = make_scheduler_role()
        
        assert "execute_command" not in role.allowlist
        assert "delegate" not in role.allowlist


class TestRoleRegistry:
    """Test role registry and lookup."""
    
    def test_builtin_roles_contains_file_scout(self):
        """BUILTIN_ROLES includes file-scout."""
        assert "file-scout" in BUILTIN_ROLES
    
    def test_builtin_roles_contains_scheduler(self):
        """BUILTIN_ROLES includes scheduler."""
        assert "scheduler" in BUILTIN_ROLES
    
    def test_list_roles_returns_all(self):
        """list_roles() returns all built-in role names."""
        roles = list_roles()
        assert "file-scout" in roles
        assert "scheduler" in roles
        assert len(roles) >= 2
    
    def test_get_role_file_scout(self):
        """get_role() returns file-scout configuration."""
        role = get_role("file-scout")
        assert isinstance(role, SubAgentRole)
        assert role.name == "file-scout"
    
    def test_get_role_scheduler(self):
        """get_role() returns scheduler configuration."""
        role = get_role("scheduler")
        assert isinstance(role, SubAgentRole)
        assert role.name == "scheduler"
    
    def test_get_role_unknown_raises(self):
        """get_role() with unknown name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown sub-agent role"):
            get_role("nonexistent-role")
    
    def test_get_role_error_lists_available(self):
        """get_role() error message lists available roles."""
        try:
            get_role("bad-role")
            assert False, "Should have raised ValueError"
        except ValueError as err:
            error_msg = str(err)
            assert "Available:" in error_msg
            assert "file-scout" in error_msg
            assert "scheduler" in error_msg


class TestRoleConsistency:
    """Test that role configurations are consistent and correct."""
    
    def test_all_roles_deny_delegation(self):
        """All sub-agent roles exclude the delegate tool (no nesting)."""
        for role_name in list_roles():
            role = get_role(role_name)
            assert "delegate" not in role.allowlist, f"{role_name} should not allow delegate"
    
    def test_all_roles_have_reasonable_limits(self):
        """All roles have non-zero limits."""
        for role_name in list_roles():
            role = get_role(role_name)
            assert role.max_iterations > 0
            assert role.max_result_chars > 0
    
    def test_all_roles_have_allowlist(self):
        """All roles have a non-empty allowlist."""
        for role_name in list_roles():
            role = get_role(role_name)
            assert isinstance(role.allowlist, list)
            assert len(role.allowlist) > 0, f"{role_name} should have tools in allowlist"
