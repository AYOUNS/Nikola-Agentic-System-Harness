#!/usr/bin/env python3
"""
Display the Mensa menu in a beautiful CLI format with colors and emojis.
"""

import json
import sys
from datetime import datetime


# ANSI color codes
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    # Colors
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    
    # Background colors
    BG_BLUE = '\033[44m'
    BG_GREEN = '\033[42m'


# Emoji mappings
SECTION_EMOJIS = {
    'Suppen': '🍲',
    'Hauptgerichte': '🍽️',
    'Beilagen': '🥗',
    'Desserts': '🍰',
    'Salate': '🥬',
    'Vegetarisch': '🥕',
    'Vegan': '🌱',
}

DIET_EMOJIS = {
    'vegetarisch': '🥕',
    'Vegan': '🌱',
    'Fleisch': '🍖',
    'Fisch': '🐟',
    'Schwein': '🐷',
    'Rind': '🐄',
    'Geflügel': '🐓',
    'Lamm': '🐑',
    'mensaVital': '💚',
    'bio': '🌿',
}


def get_section_emoji(section):
    """Get emoji for a section, or default."""
    for key, emoji in SECTION_EMOJIS.items():
        if key.lower() in section.lower():
            return emoji
    return '📋'


def get_diet_icons(icons):
    """Convert diet icons to emojis."""
    emojis = []
    for icon in icons:
        for key, emoji in DIET_EMOJIS.items():
            if key.lower() in icon.lower():
                emojis.append(emoji)
                break
    return ' '.join(emojis) if emojis else ''


def print_header():
    """Print a fancy header."""
    width = 80
    print()
    print(f"{Colors.BOLD}{Colors.BG_BLUE}{Colors.WHITE}" + " " * width + Colors.RESET)
    title = "🍴  MENSA PASSAU - TODAY'S MENU  🍴"
    padding = (width - len(title)) // 2
    print(f"{Colors.BOLD}{Colors.BG_BLUE}{Colors.WHITE}" + " " * padding + title + " " * (width - padding - len(title)) + Colors.RESET)
    print(f"{Colors.BOLD}{Colors.BG_BLUE}{Colors.WHITE}" + " " * width + Colors.RESET)
    print()
    
    # Date
    date_str = datetime.now().strftime('%A, %d. %B %Y')
    print(f"{Colors.CYAN}{Colors.BOLD}📅 {date_str}{Colors.RESET}")
    print()


def print_separator():
    """Print a separator line."""
    print(f"{Colors.DIM}{'─' * 80}{Colors.RESET}")


def print_meal(meal, is_last=False):
    """Print a single meal with formatting."""
    # Meal name with allergens
    name = meal['name']
    allergens = meal.get('allergens', '')
    if allergens:
        name_display = f"{Colors.BOLD}{Colors.WHITE}{name}{Colors.RESET} {Colors.DIM}({allergens}){Colors.RESET}"
    else:
        name_display = f"{Colors.BOLD}{Colors.WHITE}{name}{Colors.RESET}"
    
    # Diet icons
    diet_icons = get_diet_icons(meal.get('icons', []))
    if diet_icons:
        print(f"  {diet_icons}  {name_display}")
    else:
        print(f"  • {name_display}")
    
    # Prices
    student = meal.get('student_price', '')
    employee = meal.get('employee_price', '')
    guest = meal.get('guest_price', '')
    
    if student or employee or guest:
        price_parts = []
        if student:
            price_parts.append(f"{Colors.GREEN}👨‍🎓 {student}{Colors.RESET}")
        if employee:
            price_parts.append(f"{Colors.YELLOW}👔 {employee}{Colors.RESET}")
        if guest:
            price_parts.append(f"{Colors.MAGENTA}🎫 {guest}{Colors.RESET}")
        
        print(f"    {' • '.join(price_parts)}")
    
    if not is_last:
        print()


def print_section(section_name, meals):
    """Print a section with all its meals."""
    emoji = get_section_emoji(section_name)
    print(f"\n{Colors.BOLD}{Colors.CYAN}{emoji}  {section_name.upper()}{Colors.RESET}")
    print_separator()
    print()
    
    for i, meal in enumerate(meals):
        is_last = (i == len(meals) - 1)
        print_meal(meal, is_last)


def print_footer():
    """Print footer with legend."""
    print()
    print_separator()
    print()
    print(f"{Colors.DIM}Legend: 👨‍🎓 Student  •  👔 Employee  •  🎫 Guest{Colors.RESET}")
    print(f"{Colors.DIM}        🥕 Vegetarian  •  🌱 Vegan  •  💚 MensaVital  •  🌿 Bio{Colors.RESET}")
    print()


def display_menu(data):
    """Main display function."""
    if not data.get('success', False):
        error = data.get('error', 'Unknown error')
        print(f"\n{Colors.RED}{Colors.BOLD}❌ Error:{Colors.RESET} {error}\n")
        return False
    
    meals = data.get('meals', [])
    
    if not meals:
        print_header()
        print(f"{Colors.YELLOW}🏖️  No meals available today. The Mensa might be closed.{Colors.RESET}\n")
        return True
    
    # Group meals by section
    sections = {}
    for meal in meals:
        section = meal.get('section', 'Other')
        if section not in sections:
            sections[section] = []
        sections[section].append(meal)
    
    # Display
    print_header()
    
    # Define preferred section order
    section_order = ['Suppen', 'Hauptgerichte', 'Vegetarisch', 'Vegan', 'Beilagen', 'Salate', 'Desserts']
    
    # Print sections in order
    displayed_sections = set()
    for section_name in section_order:
        for section_key in sections.keys():
            if section_name.lower() in section_key.lower():
                print_section(section_key, sections[section_key])
                displayed_sections.add(section_key)
    
    # Print remaining sections
    for section_name, section_meals in sections.items():
        if section_name not in displayed_sections:
            print_section(section_name, section_meals)
    
    print_footer()
    return True


def main():
    """Main entry point."""
    try:
        # Read JSON from stdin or file
        if len(sys.argv) > 1:
            with open(sys.argv[1], 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = json.load(sys.stdin)
        
        success = display_menu(data)
        return 0 if success else 1
    
    except json.JSONDecodeError as e:
        print(f"\n{Colors.RED}{Colors.BOLD}❌ JSON Error:{Colors.RESET} {str(e)}\n", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n{Colors.RED}{Colors.BOLD}❌ Error:{Colors.RESET} {str(e)}\n", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
