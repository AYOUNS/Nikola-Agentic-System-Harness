#!/usr/bin/env python3
"""Visual design test script - demonstrates the new CLI rendering."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.cli.ui import ConsoleUI


def test_color_palette():
    """Display the color palette."""
    from agent.cli.ui import COLORS
    
    ui = ConsoleUI()
    ui.console.print("\n=== Color Palette Test ===\n")
    
    for name, color in COLORS.items():
        ui.console.print(f"[{color}]●[/] {name}: {color}")


def test_error_box():
    """Test the red-bordered error box."""
    ui = ConsoleUI()
    
    ui.console.print("\n=== Error Box Test ===\n")
    
    # Single-part error
    ui.sink("error", "Something went wrong")
    
    ui.console.print()
    
    # Two-part error
    ui.sink("error", "Loop terminated: max iterations (30) reached.|The task may be too complex. Try breaking it into smaller steps or provide more specific guidance.")


def test_permission_box():
    """Test the amber-bordered permission box."""
    ui = ConsoleUI()
    
    ui.console.print("\n=== Permission Box Test (visual only - no interaction) ===\n")
    
    # We can't actually test the interactive menu without blocking,
    # but we can show what the box looks like
    from rich.text import Text
    from rich.panel import Panel
    from agent.cli.ui import COLORS
    
    content = Text()
    content.append("⚠ Permission needed\n", style=f"bold {COLORS['caution_text']}")
    content.append("This will write to a file\n\n", style=COLORS['primary'])
    content.append("  path: /workspace/output.txt\n", style=COLORS['secondary'])
    content.append("  content: Hello, world! This is...\n", style=COLORS['secondary'])
    content.append("\n", style="")
    content.append("Allow ", style=COLORS['primary'])
    content.append("write_file", style=COLORS['tool'])
    content.append(" to run?", style=COLORS['primary'])
    
    panel = Panel(
        content,
        border_style=COLORS['caution_border'],
        padding=(1, 2),
        expand=False,
    )
    ui.console.print(panel)
    
    ui.console.print("\n(In actual use, this would show an interactive menu)")


def test_tool_trace():
    """Test tool trace formatting."""
    ui = ConsoleUI()
    
    ui.console.print("\n=== Tool Trace Test ===\n")
    
    # Tool call
    ui.sink("tool", "write_file(path: output.txt, content: Hello, world!)\n")
    
    # Success result
    ui.sink("tool", "⎿ File written successfully\n")
    
    # Another tool call
    ui.sink("tool", "read_file(path: missing.txt)\n")
    
    # Error result (plain, not boxed)
    ui.sink("tool", "⎿ Error: File not found: missing.txt\n")
    
    # Tool call with success marker
    ui.sink("tool", "create_folder(path: output)\n")
    ui.sink("tool", "⎿ ✓ Folder created\n")


def test_thinking_display():
    """Test thinking/reasoning display."""
    ui = ConsoleUI()
    
    ui.console.print("\n=== Thinking Display Test ===\n")
    
    # Simulate reasoning
    ui.sink("reasoning", "I need to first check if the file exists...")
    ui.sink("reasoning", " then read its contents and process them.")
    
    # End thinking manually (in real use, this happens automatically)
    from agent.cli.ui import COLORS
    from rich.text import Text
    ui.console.print(Text("✻ Thought for 2.3s", style=COLORS['secondary']))


def test_prompt_arrow():
    """Test prompt arrow display."""
    ui = ConsoleUI()
    
    ui.console.print("\n=== Prompt Arrow Test ===\n")
    
    from agent.cli.ui import COLORS
    from rich.text import Text
    
    text = Text()
    text.append("❯", style=COLORS['accent'])
    text.append(" This is what the prompt looks like", style=COLORS['primary'])
    ui.console.print(text)


def test_info_messages():
    """Test info, hint, and error messages."""
    ui = ConsoleUI()
    
    ui.console.print("\n=== Info Messages Test ===\n")
    
    ui.info("This is an informational message")
    ui.hint("This is a hint for the user")
    ui.error("This is a routine error (not boxed)")


def main():
    """Run all visual tests."""
    test_color_palette()
    test_prompt_arrow()
    test_tool_trace()
    test_thinking_display()
    test_info_messages()
    test_error_box()
    test_permission_box()
    
    print("\n=== All visual tests complete ===\n")


if __name__ == "__main__":
    main()
