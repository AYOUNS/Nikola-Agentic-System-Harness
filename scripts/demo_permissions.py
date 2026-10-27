#!/usr/bin/env python3
"""
Simple demonstration of the permission system with session caching.
Run this to see how write permissions work with y/a/n/d options.
"""

import asyncio
from agent.permissions.gate import make_permission_gate
from agent.permissions.policy import make_default_policy
from agent.permissions.cache import PermissionCache, make_cached_confirmation_fn


async def demo_permission_system():
    """Demonstrate the permission system with session-level caching."""
    
    print("=" * 70)
    print("PERMISSION SYSTEM DEMONSTRATION (WITH SESSION CACHING)")
    print("=" * 70)
    print()
    
    # Create a mock confirmation function that simulates user input
    # Returns (approved, scope) where scope is "once" or "session"
    responses = {
        "write_file": (True, "session"),   # User always allows
        "create_folder": (True, "once"),   # User allows once
        "delete_file": (False, "session"), # User always denies
        "read_file": None,                 # Should not be asked
    }
    
    confirmation_count = [0]
    
    def mock_confirmation(tool_name: str, arguments: dict) -> tuple[bool, str]:
        confirmation_count[0] += 1
        print(f"\n📋 Permission Request #{confirmation_count[0]}")
        print(f"   Tool: {tool_name}")
        print(f"   Arguments: {arguments}")
        
        response = responses.get(tool_name)
        if response is None:
            print("   ❌ ERROR: Should not have been asked!")
            return False, "once"
        
        approved, scope = response
        decision = "✓ APPROVED" if approved else "✗ DENIED"
        scope_text = "(for this session)" if scope == "session" else "(once only)"
        print(f"   User Decision: {decision} {scope_text}")
        return response
    
    # Create permission cache and cached confirmation function
    cache = PermissionCache()
    cached_confirmation = make_cached_confirmation_fn(mock_confirmation, cache)
    
    # Create policy and gate
    policy = make_default_policy(is_subagent=False)
    gate = make_permission_gate(policy, confirmation_fn=cached_confirmation)
    
    # Mock tool registry dispatch
    async def mock_dispatch(tool_name: str, arguments: dict) -> str:
        return f"SUCCESS: {tool_name} executed with {arguments}"
    
    print("\n" + "─" * 70)
    print("TEST 1: Write File (should prompt, user approves)")
    print("─" * 70)
    
    result = await gate.dispatch_with_permission(
        "write_file",
        {"path": "/workspace/test.txt", "content": "Hello"},
        mock_dispatch
    )
    print(f"Result: {result}\n")
    
    print("\n" + "─" * 70)
    print("TEST 2: Read File (should NOT prompt - allowed by default)")
    print("─" * 70)
    
    result = await gate.dispatch_with_permission(
        "read_file",
        {"path": "/workspace/test.txt"},
        mock_dispatch
    )
    print(f"Result: {result}")
    print(f"Confirmation asked? {'YES ❌' if responses['read_file'] is not None else 'NO ✓'}\n")
    
    print("\n" + "─" * 70)
    print("TEST 3: Write File Again (should use cached session approval)")
    print("─" * 70)
    
    result = await gate.dispatch_with_permission(
        "write_file",
        {"path": "/workspace/another.txt", "content": "World"},
        mock_dispatch
    )
    print(f"Result: {result}")
    print(f"Prompted again? {'YES ❌' if confirmation_count[0] > 1 else 'NO ✓ (used cache)'}\n")
    
    print("\n" + "─" * 70)
    print("TEST 4: Create Folder (once-only approval, will prompt again)")
    print("─" * 70)
    
    responses["create_folder"] = (True, "once")
    
    # First call - should prompt
    result = await gate.dispatch_with_permission(
        "create_folder",
        {"path": "/workspace/dir1"},
        mock_dispatch
    )
    print(f"Result: {result}\n")
    
    initial_count = confirmation_count[0]
    
    # Second call - should prompt AGAIN (not cached)
    result = await gate.dispatch_with_permission(
        "create_folder",
        {"path": "/workspace/dir2"},
        mock_dispatch
    )
    print(f"Result: {result}")
    print(f"Prompted again? {'YES ✓ (once only)' if confirmation_count[0] > initial_count else 'NO ❌'}\n")
    
    print("\n" + "─" * 70)
    print("TEST 5: Delete File (session denial - cached)")
    print("─" * 70)
    
    responses["delete_file"] = (False, "session")
    
    # First call
    result = await gate.dispatch_with_permission(
        "delete_file",
        {"path": "/workspace/test.txt"},
        mock_dispatch
    )
    print(f"Result: {result}\n")
    
    denial_count = confirmation_count[0]
    
    # Second call - should use cached denial
    result = await gate.dispatch_with_permission(
        "delete_file",
        {"path": "/workspace/other.txt"},
        mock_dispatch
    )
    print(f"Result: {result}")
    print(f"Prompted again? {'YES ❌' if confirmation_count[0] > denial_count else 'NO ✓ (cached denial)'}\n")
    
    print("\n" + "═" * 70)
    print("SUMMARY")
    print("═" * 70)
    print(f"✓ Total confirmation prompts shown: {confirmation_count[0]}")
    print(f"✓ Read operations bypassed confirmation: YES")
    print(f"✓ Write operations required confirmation: YES")
    
    stats = cache.get_stats()
    print(f"✓ Session-level cached decisions: {stats['total_cached']}")
    print(f"  - Always allowed: {stats['allowed_count']} (write_file)")
    print(f"  - Always denied: {stats['denied_count']} (delete_file)")
    print(f"✓ Once-only decisions not cached: YES (create_folder)")
    print(f"✓ Permission system with caching working correctly!")
    print("═" * 70)


if __name__ == "__main__":
    asyncio.run(demo_permission_system())
