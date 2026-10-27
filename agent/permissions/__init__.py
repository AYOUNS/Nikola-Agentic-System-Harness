"""Permission management for agent tool execution.

Public exports:
    PermissionModule    - Deep module for all permission logic
    PermissionDecision  - Typed result from check()
    PermissionAction    - Enum: ALLOW/DENY/ASK
    NO_RETRY_DIRECTIVE  - Shared wording telling the model a denial is final
"""

from agent.permissions.module import (
    NO_RETRY_DIRECTIVE,
    PermissionAction,
    PermissionDecision,
    PermissionModule,
)

__all__ = [
    "NO_RETRY_DIRECTIVE",
    "PermissionAction",
    "PermissionDecision",
    "PermissionModule",
]
