"""Deep permission module for agent tool execution.

Consolidates permission checking, rule evaluation, caching, and policy management
into a single interface. Implements first-match-wins rule evaluation and
sub-agent auto-deny.

Public interface:
    PermissionModule.check(tool, args) → PermissionDecision
    PermissionModule.record_decision(tool, args, approved, scope)
    PermissionModule.stats() → dict

Internal implementation:
    - YAML parsing and rule engine
    - Session-level confirmation cache (argument-aware)
    - Policy defaults (read-only allowed, write ask/deny, dangerous deny)
    - Sub-agent auto-deny logic
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# A denial is a permanent fact for the rest of the turn: nothing about retrying
# the same call can change the policy that produced it. Every denial message
# says so, or the model reads a bare "denied" as a transient failure and spends
# the iteration budget re-issuing the identical call.
NO_RETRY_DIRECTIVE = (
    "Do NOT retry this tool - the result will be identical. "
    "Use a different tool, or tell the user what permission change would be needed."
)


# =============================================================================
# Public Types
# =============================================================================

class PermissionAction(Enum):
    """Permission actions for tool execution."""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class PermissionDecision:
    """Result of a permission check.
    
    Attributes:
        action: Whether to allow, deny, or ask for confirmation
        error_message: Human-readable error when denied (None if allowed)
        was_cached: Whether this decision came from session cache
    """
    action: PermissionAction
    error_message: str | None = None
    was_cached: bool = False


# =============================================================================
# Internal Types
# =============================================================================

@dataclass
class _PermissionRule:
    """A single permission rule from permissions.yaml.
    
    Rules are evaluated in order; first match wins.
    """
    tool: str  # Glob pattern matched against tool name
    action: PermissionAction
    path_pattern: str | None = None  # Optional glob for 'path' argument

    def matches(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Return True if this rule applies to the given tool call."""
        if not fnmatch(tool_name, self.tool):
            return False
        
        if self.path_pattern is not None:
            path_arg = arguments.get("path", "")
            if not fnmatch(str(path_arg), self.path_pattern):
                return False
        
        return True


class _PermissionCache:
    """Session-level cache for user confirmation decisions.
    
    Cache keys are (tool_name, path) tuples where path is extracted from
    arguments. This makes the cache path-aware: confirming write_file for
    tests/foo.py does not implicitly allow write_file for src/prod.py.
    """
    
    def __init__(self):
        self._allowed: set[tuple[str, str | None]] = set()
        self._denied: set[tuple[str, str | None]] = set()
    
    def _make_key(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, str | None]:
        """Create cache key from tool name and arguments."""
        path = arguments.get("path")
        return (tool_name, path)
    
    def get(self, tool_name: str, arguments: dict[str, Any]) -> bool | None:
        """Get cached decision. Returns True/False if cached, None if not."""
        key = self._make_key(tool_name, arguments)
        if key in self._allowed:
            return True
        if key in self._denied:
            return False
        return None
    
    def record_allow(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Cache an allow decision for this tool+args combination."""
        key = self._make_key(tool_name, arguments)
        self._allowed.add(key)
        self._denied.discard(key)
    
    def record_deny(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Cache a deny decision for this tool+args combination."""
        key = self._make_key(tool_name, arguments)
        self._denied.add(key)
        self._allowed.discard(key)
    
    def stats(self) -> dict[str, int]:
        """Return cache statistics."""
        return {
            "allowed_count": len(self._allowed),
            "denied_count": len(self._denied),
            "total_cached": len(self._allowed) + len(self._denied),
        }


# =============================================================================
# Public Module
# =============================================================================

class PermissionModule:
    """Deep module for tool permission management.
    
    Consolidates all permission logic behind a narrow interface:
    - check(tool, args) → decision
    - record_decision(tool, args, approved, scope)
    - stats() → cache stats
    
    Internal implementation handles:
    - YAML loading and rule evaluation (first-match-wins)
    - Policy defaults (read-only allowed, write ask, dangerous deny)
    - Session-level caching with argument-aware keys
    - Sub-agent auto-deny (ASK → DENY)
    """
    
    def __init__(
        self,
        is_subagent: bool = False,
        permissions_file: Path | None = None,
    ):
        """Initialize permission module.
        
        Args:
            is_subagent: Whether this is for a sub-agent (enables auto-deny for ASK policies)
            permissions_file: Optional path to permissions.yaml (searches defaults if None)
        """
        self.is_subagent = is_subagent
        self._cache = _PermissionCache()
        
        # Try to load permissions.yaml
        self._rules, self._default_action = self._load_permissions(permissions_file)
        
        # Build policy defaults
        self._allowlist = self._make_default_allowlist()
        self._denylist = self._make_default_denylist()
        self._asklist = self._make_default_asklist()
    
    # -------------------------------------------------------------------------
    # Public Interface
    # -------------------------------------------------------------------------
    
    def check(self, tool_name: str, arguments: dict[str, Any]) -> PermissionDecision:
        """Check if a tool call is permitted.

        Returns a typed decision with action, error message (if denied), and
        whether the decision came from cache.

        Evaluation order:
        1. Resolve policy action: YAML rules (first-match-wins),
           then explicit lists, then default.
        2. If policy says ASK — always prompt; cache is bypassed because
           require-user-confirmation means every invocation must be approved.
        3. If policy says ALLOW or DENY — consult the session cache so that
           the user's explicit "don't ask again" choice is honoured.
        4. Apply sub-agent auto-deny.

        Args:
            tool_name: Name of the tool to check
            arguments: Tool arguments (used for path patterns and caching)

        Returns:
            PermissionDecision with action and optional error message
        """
        # 1. Resolve the policy action first — before touching the cache.
        action, is_from_rule = self._resolve_policy_action(tool_name, arguments)

        # 2. ASK tools always prompt — the cache must not short-circuit them.
        #    "require-user-confirmation" means every call, not just the first.
        if action == PermissionAction.ASK:
            return self._make_decision(tool_name, PermissionAction.ASK, is_from_rule)

        # 3. For ALLOW / DENY, the cache can honour a prior explicit decision.
        cached = self._cache.get(tool_name, arguments)
        if cached is not None:
            if cached:
                return PermissionDecision(action=PermissionAction.ALLOW, was_cached=True)
            else:
                return PermissionDecision(
                    action=PermissionAction.DENY,
                    error_message=(
                        f"Tool '{tool_name}' was denied by the user in this session. "
                        f"{NO_RETRY_DIRECTIVE}"
                    ),
                    was_cached=True,
                )

        return self._make_decision(tool_name, action, is_from_rule)

    def _resolve_policy_action(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[PermissionAction, bool]:
        """Return the action mandated by policy rules alone, ignoring the cache.

        Evaluates YAML rules in order (first-match-wins), then
        the explicit allow/deny/ask lists, then the configured default.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments (used for path_pattern matching)

        Returns:
            (action, is_from_rule) — is_from_rule is True when the action came
            from a permissions.yaml rule rather than a built-in policy default,
            so the denial message can name what the user would have to edit.
        """
        if self._rules:
            for rule in self._rules:
                if rule.matches(tool_name, arguments):
                    return rule.action, True

        if tool_name in self._denylist:
            return PermissionAction.DENY, False
        if tool_name in self._allowlist:
            return PermissionAction.ALLOW, False
        if tool_name in self._asklist:
            return PermissionAction.ASK, False

        return self._default_action, False
    
    def record_decision(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        approved: bool,
        scope: str,
    ) -> None:
        """Record a user confirmation decision.
        
        Called after user responds to an ASK prompt. Only "session" scope
        decisions are cached; "once" decisions are not stored.
        
        Args:
            tool_name: Name of the tool
            arguments: Tool arguments (for cache key)
            approved: Whether user approved (True) or denied (False)
            scope: "once" (one-time) or "session" (cache for session)
        """
        if scope == "session":
            if approved:
                self._cache.record_allow(tool_name, arguments)
            else:
                self._cache.record_deny(tool_name, arguments)
    
    def stats(self) -> dict[str, int]:
        """Return permission cache statistics.
        
        Returns:
            Dictionary with allowed_count, denied_count, total_cached
        """
        return self._cache.stats()
    
    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------
    
    def _make_decision(
        self,
        tool_name: str,
        action: PermissionAction,
        is_from_rule: bool,
    ) -> PermissionDecision:
        """Convert action to PermissionDecision, applying sub-agent auto-deny if needed."""
        # Apply sub-agent auto-deny: ASK becomes DENY for sub-agents
        if self.is_subagent and action == PermissionAction.ASK:
            return PermissionDecision(
                action=PermissionAction.DENY,
                error_message=(
                    f"Tool '{tool_name}' requires confirmation, but sub-agents cannot "
                    f"prompt the user. {NO_RETRY_DIRECTIVE} "
                    f"Update role configuration to use 'allow' or 'deny'."
                ),
            )

        # Generate error messages for explicit denies
        if action == PermissionAction.DENY:
            source = "a permissions.yaml rule" if is_from_rule else "permission policy"
            return PermissionDecision(
                action=PermissionAction.DENY,
                error_message=(
                    f"Tool '{tool_name}' is denied by {source}. {NO_RETRY_DIRECTIVE}"
                ),
            )
        
        # ALLOW and ASK actions
        return PermissionDecision(action=action)
    
    def _load_permissions(
        self,
        explicit_path: Path | None,
    ) -> tuple[list[_PermissionRule], PermissionAction]:
        """Load permissions.yaml if it exists.

        Search order:
        1. explicit_path (if provided)
        2. ${WORKSPACE}/.nikola/permissions.yaml
        3. ./permissions.yaml
        4. ~/.nikola/permissions.yaml

        Returns (rules, default_action). If no file found, returns empty rules
        and ASK as default.
        """
        # Find file
        if explicit_path is not None:
            if not explicit_path.exists():
                raise FileNotFoundError(f"Permissions file not found: {explicit_path}")
            path = explicit_path
        else:
            # Imported here rather than at module scope to keep the permission
            # module free of a hard dependency on the tool layer.
            from agent.tools.filesystem import get_workspace_root

            # Most specific first. The workspace copy wins over the project-root
            # one for the same reason a workspace skill overrides a built-in
            # (agent/skills/loader.py): the workspace is what the operator mounts
            # and edits, while the project root is baked into the image at /app —
            # under the container's WORKDIR, not the mount, so a bare
            # Path("permissions.yaml") never sees the workspace at all.
            #
            # The workspace policy lives in .nikola/, where the rest of the harness
            # config lives (mcp.json, rules/, skills/) and matching the
            # home-directory tier below.
            workspace_root = get_workspace_root()
            candidates = [
                workspace_root / ".nikola" / "permissions.yaml",
                Path("permissions.yaml"),
                Path.home() / ".nikola" / "permissions.yaml",
            ]
            path = None
            for candidate in candidates:
                if candidate.exists():
                    path = candidate
                    break

            # The workspace root is no longer searched. A file left there would
            # otherwise be ignored in silence and the agent would run under the
            # project-root policy instead — say so rather than quietly downgrading.
            stale = workspace_root / "permissions.yaml"
            if stale.exists():
                logger.warning(
                    f"Ignoring {stale}: the workspace policy is now read from "
                    f"{workspace_root / '.nikola' / 'permissions.yaml'}. Move the file there."
                )

            if path is None:
                # No permissions file found - use defaults
                logger.debug("No permissions.yaml found - using policy defaults")
                return [], PermissionAction.ASK
        
        # Load and parse YAML
        logger.info(f"Loading permissions from {path}")
        with path.open("r") as fh:
            raw = yaml.safe_load(fh) or {}
        
        # Parse default action
        default_str = raw.get("default", "ask")
        action_map = {
            "allow": PermissionAction.ALLOW,
            "deny": PermissionAction.DENY,
            "ask": PermissionAction.ASK,
        }
        if default_str not in action_map:
            raise ValueError(
                f"Invalid 'default' in {path}: expected allow/deny/ask, got {default_str!r}"
            )
        default_action = action_map[default_str]
        
        # Parse rules
        raw_rules = raw.get("rules", []) or []
        if not isinstance(raw_rules, list):
            raise TypeError(f"'rules' in {path} must be a list")
        
        rules = []
        for idx, entry in enumerate(raw_rules):
            if not isinstance(entry, dict):
                raise TypeError(f"rules[{idx}] in {path} must be a mapping")
            
            # tool (required)
            tool = entry.get("tool")
            if not tool or not isinstance(tool, str):
                raise ValueError(f"rules[{idx}] in {path} missing required 'tool' field")
            
            # action (required)
            action_str = entry.get("action")
            if not action_str or action_str not in action_map:
                raise ValueError(
                    f"rules[{idx}] in {path} 'action' must be allow/deny/ask, got {action_str!r}"
                )
            action = action_map[action_str]
            
            # path_pattern (optional)
            path_pattern = entry.get("path_pattern")
            if path_pattern is not None and not isinstance(path_pattern, str):
                raise ValueError(f"rules[{idx}] in {path} 'path_pattern' must be a string")
            
            rules.append(_PermissionRule(tool=tool, action=action, path_pattern=path_pattern))
        
        logger.debug(f"Loaded {len(rules)} permission rule(s) with default={default_str!r}")
        return rules, default_action
    
    def _make_default_allowlist(self) -> set[str]:
        """Build default allowlist for read-only tools."""
        return {
            "read_file",
            "list_files",
            "search_text",
            "glob",
            "grep",
            "search_memory",
        }
    
    def _make_default_denylist(self) -> set[str]:
        """Build default denylist for dangerous tools."""
        return {
            "execute_command",
            "delegate",  # Sub-agents cannot spawn sub-agents
            "delegate_parallel",
        }
    
    def _make_default_asklist(self) -> set[str]:
        """Build default asklist for write tools."""
        return {
            "write_file",
            "create_file",
            "modify_file",
            "create_folder",
            "remember",
            "search_and_replace",
        }
