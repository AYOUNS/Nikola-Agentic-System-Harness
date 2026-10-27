"""Basic smoke tests for the new PermissionModule.

Verifies the deepened permission architecture works correctly.
"""

import logging

import pytest

from agent.permissions import NO_RETRY_DIRECTIVE, PermissionAction, PermissionModule


def test_permission_module_basic_allow():
    """Test that read_file is allowed by default."""
    module = PermissionModule(is_subagent=False)
    
    decision = module.check("read_file", {"path": "test.txt"})
    
    assert decision.action == PermissionAction.ALLOW
    assert decision.error_message is None
    assert decision.was_cached is False


def test_permission_module_basic_deny():
    """Test that execute_command is denied by default."""
    module = PermissionModule(is_subagent=False)
    
    decision = module.check("execute_command", {"command": "ls"})
    
    assert decision.action == PermissionAction.DENY
    assert decision.error_message is not None
    assert "denied" in decision.error_message.lower()


def test_permission_module_basic_ask():
    """Test that write_file requires confirmation by default."""
    module = PermissionModule(is_subagent=False)
    
    decision = module.check("write_file", {"path": "test.txt"})
    
    assert decision.action == PermissionAction.ASK


def test_permission_module_cache_argument_aware():
    """Cache keys are (tool, path) pairs — approving one path does not cover another.

    Uses an ALLOW-policy tool (read_file) so we can test the cache path without
    hitting the ASK-bypass introduced to enforce require-user-confirmation.
    """
    module = PermissionModule(is_subagent=False)

    # Explicitly cache an allow decision for one specific path
    module.record_decision("read_file", {"path": "tests/foo.py"}, approved=True, scope="session")

    # That exact path is cached
    decision1 = module.check("read_file", {"path": "tests/foo.py"})
    assert decision1.action == PermissionAction.ALLOW
    assert decision1.was_cached is True

    # A different path is NOT cached — policy resolves it normally (ALLOW for read_file)
    decision2 = module.check("read_file", {"path": "src/prod.py"})
    assert decision2.action == PermissionAction.ALLOW
    assert decision2.was_cached is False


def test_permission_module_subagent_auto_deny():
    """Test that sub-agents convert ASK to DENY."""
    module = PermissionModule(is_subagent=True)
    
    # write_file is ASK for parent, but should be DENY for sub-agent
    decision = module.check("write_file", {"path": "test.txt"})
    
    assert decision.action == PermissionAction.DENY
    assert decision.error_message is not None
    assert "sub-agent" in decision.error_message.lower()
    assert "cannot prompt" in decision.error_message.lower()


def test_permission_module_cache_once_scope():
    """Test that 'once' scope decisions are NOT cached."""
    module = PermissionModule(is_subagent=False)
    
    # Record with scope="once"
    module.record_decision("write_file", {"path": "test.txt"}, approved=True, scope="once")
    
    # Should NOT be cached
    decision = module.check("write_file", {"path": "test.txt"})
    assert decision.action == PermissionAction.ASK
    assert decision.was_cached is False


def test_permission_module_cache_session_scope_allow_tool():
    """Test that 'session' approval IS cached for ALLOW-policy tools."""
    module = PermissionModule(is_subagent=False)

    # read_file is in the allowlist — cache should short-circuit it
    module.record_decision("read_file", {"path": "test.txt"}, approved=True, scope="session")

    decision = module.check("read_file", {"path": "test.txt"})
    assert decision.action == PermissionAction.ALLOW
    assert decision.was_cached is True


def test_ask_policy_always_prompts_even_after_session_approval():
    """ASK-policy tools must prompt on every call; cache must not bypass them.

    This is the core require-user-confirmation guarantee: recording a session
    approval for an ASK tool must NOT make subsequent calls skip the prompt.
    """
    module = PermissionModule(is_subagent=False)

    # Simulate the user picking "Yes, don't ask again for this tool"
    module.record_decision("write_file", {"path": "test.txt"}, approved=True, scope="session")

    # write_file is in the asklist — policy says ASK — cache must be bypassed
    decision = module.check("write_file", {"path": "test.txt"})
    assert decision.action == PermissionAction.ASK
    assert decision.was_cached is False


def _write_permissions(tmp_path, name: str, body: str):
    """Write a permissions file under tmp_path and return its path."""
    path = tmp_path / name
    path.write_text(body)
    return path


RULE_DENIES_EXECUTE = "default: ask\nrules:\n  - tool: execute_command\n    action: deny\n"
NO_RULES = "default: ask\nrules: []\n"


def test_every_denial_tells_the_model_not_to_retry(tmp_path):
    """All four denial paths must carry the no-retry directive.

    A bare "denied" reads as a transient failure, and the model burns the
    iteration budget re-issuing the identical call until a guard fires.
    """
    by_rule = _write_permissions(tmp_path, "with-rule.yaml", RULE_DENIES_EXECUTE)
    no_rules = _write_permissions(tmp_path, "no-rules.yaml", NO_RULES)

    # 1. Denied by a permissions.yaml rule
    decision = PermissionModule(permissions_file=by_rule).check(
        "execute_command", {"command": "ls -la"}
    )
    assert decision.action == PermissionAction.DENY
    assert NO_RETRY_DIRECTIVE in decision.error_message

    # 2. Denied by the built-in denylist (no rule matches the tool)
    decision = PermissionModule(permissions_file=no_rules).check(
        "execute_command", {"command": "ls -la"}
    )
    assert decision.action == PermissionAction.DENY
    assert NO_RETRY_DIRECTIVE in decision.error_message

    # 3. Sub-agent auto-deny of an ASK tool
    decision = PermissionModule(is_subagent=True, permissions_file=no_rules).check(
        "write_file", {"path": "test.txt"}
    )
    assert decision.action == PermissionAction.DENY
    assert NO_RETRY_DIRECTIVE in decision.error_message

    # 4. Cached user denial (an ALLOW-policy tool the user denied for the session)
    module = PermissionModule(permissions_file=no_rules)
    module.record_decision("read_file", {"path": "secret.txt"}, approved=False, scope="session")
    decision = module.check("read_file", {"path": "secret.txt"})
    assert decision.action == PermissionAction.DENY
    assert decision.was_cached is True
    assert NO_RETRY_DIRECTIVE in decision.error_message


def test_denial_message_names_permissions_yaml_only_for_rule_denials(tmp_path):
    """A rule denial points at the file the user would edit; a policy denial does not."""
    by_rule = _write_permissions(tmp_path, "with-rule.yaml", RULE_DENIES_EXECUTE)
    no_rules = _write_permissions(tmp_path, "no-rules.yaml", NO_RULES)

    from_rule = PermissionModule(permissions_file=by_rule).check("execute_command", {})
    assert "permissions.yaml" in from_rule.error_message

    from_policy = PermissionModule(permissions_file=no_rules).check("execute_command", {})
    assert "permissions.yaml" not in from_policy.error_message
    assert "permission policy" in from_policy.error_message


def test_workspace_permissions_file_wins_over_cwd(tmp_path, monkeypatch):
    """${WORKSPACE}/.nikola/permissions.yaml is found before ./permissions.yaml.

    The container's WORKDIR is /app while the workspace mounts at /workspace, so
    without the workspace lookup a permissions.yaml the operator drops in the
    workspace is never read and silently has no effect.
    """
    workspace = tmp_path / "ws"
    (workspace / ".nikola").mkdir(parents=True)
    cwd = tmp_path / "proj"
    cwd.mkdir()

    # Same tool, opposite verdicts, so we can tell which file was loaded.
    (workspace / ".nikola" / "permissions.yaml").write_text(
        "default: ask\nrules:\n  - tool: execute_command\n    action: allow\n"
    )
    (cwd / "permissions.yaml").write_text(
        "default: ask\nrules:\n  - tool: execute_command\n    action: deny\n"
    )

    monkeypatch.chdir(cwd)
    monkeypatch.setenv("AGENT_WORKSPACE", str(workspace))

    decision = PermissionModule().check("execute_command", {"command": "ls"})
    assert decision.action == PermissionAction.ALLOW

    # With no workspace file, the cwd copy is used instead.
    (workspace / ".nikola" / "permissions.yaml").unlink()
    decision = PermissionModule().check("execute_command", {"command": "ls"})
    assert decision.action == PermissionAction.DENY


def test_workspace_root_permissions_file_is_not_read(tmp_path, monkeypatch, caplog):
    """A policy at the workspace ROOT is ignored — .nikola/ is the only workspace tier.

    Being ignored in silence is the failure mode worth guarding: the agent would
    run under the project-root policy while the operator believes their file is in
    force, so the loader warns instead.
    """
    workspace = tmp_path / "ws"
    (workspace / ".nikola").mkdir(parents=True)
    cwd = tmp_path / "proj"
    cwd.mkdir()

    (workspace / "permissions.yaml").write_text(
        "default: ask\nrules:\n  - tool: execute_command\n    action: allow\n"
    )
    (cwd / "permissions.yaml").write_text(
        "default: ask\nrules:\n  - tool: execute_command\n    action: deny\n"
    )

    monkeypatch.chdir(cwd)
    monkeypatch.setenv("AGENT_WORKSPACE", str(workspace))

    with caplog.at_level(logging.WARNING):
        decision = PermissionModule().check("execute_command", {"command": "ls"})

    # The cwd policy applied, not the workspace-root one.
    assert decision.action == PermissionAction.DENY
    assert "Ignoring" in caplog.text
    assert ".nikola" in caplog.text


def test_permission_module_stats():
    """Test that stats() returns correct cache statistics."""
    module = PermissionModule(is_subagent=False)
    
    # Initially empty
    stats = module.stats()
    assert stats["total_cached"] == 0
    
    # Cache some decisions
    module.record_decision("write_file", {"path": "a.txt"}, approved=True, scope="session")
    module.record_decision("write_file", {"path": "b.txt"}, approved=False, scope="session")
    module.record_decision("modify_file", {"path": "c.txt"}, approved=True, scope="session")
    
    stats = module.stats()
    assert stats["allowed_count"] == 2
    assert stats["denied_count"] == 1
    assert stats["total_cached"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
