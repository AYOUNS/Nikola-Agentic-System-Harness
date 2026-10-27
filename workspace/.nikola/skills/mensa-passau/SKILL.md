---
name: mensa-passau
description: Use when the user asks about the Passau mensa, cafeteria, canteen, or what's for lunch today. Fetches and displays today's menu from the University of Passau cafeteria with beautiful CLI formatting.
version: "2.0"
---

# Mensa Passau

This skill fetches and displays today's menu from the University of Passau cafeteria (Mensa) with beautiful CLI formatting including colors, emojis, and organized sections.

## Steps

1. **Fetch and display the menu** using `execute_command` with a piped command:
   ```
   python3 workspace/.nikola/skills/mensa-passau/fetch-mensa.py | python3 workspace/.nikola/skills/mensa-passau/display-mensa.py
   ```
   
   This command will:
   - Fetch today's menu from the STWNO API
   - Parse and format it beautifully with colors, emojis, and organized sections
   - Display a stunning CLI-formatted menu with:
     - 🍴 **Fancy blue header** with today's date
     - **Sections organized by type**: Suppen 🍲, Hauptgerichte 🍽️, Beilagen 🥗, Desserts 🍰, etc.
     - **Diet icons**: 🥕 Vegetarian, 🌱 Vegan, 💚 MensaVital, 🌿 Bio
     - **Color-coded prices**: 👨‍🎓 Student (green), 👔 Employee (yellow), 🎫 Guest (magenta)
     - **Allergen information** displayed in dimmed parentheses
     - Clean separators and spacing for readability
     - Helpful legend at the bottom

2. **Present the output directly**: The display script handles all formatting, so just show the command output as-is to preserve all the beautiful colors and emojis

3. **Error handling**:
   - If the script fails, it will show a clear ❌ error message
   - If no meals are available (weekend/holiday), it shows a friendly 🏖️ message
   - No need for additional explanation - the output is self-explanatory

## Notes

- The script defaults to today's date in Europe/Berlin timezone
- The API endpoint is: `https://stwno.de/infomax/daten-extern/html/speiseplan-render.php`
- Location ID is hardcoded to `UNI-P` (University of Passau)
- Terminal colors and emojis make the menu visually appealing and easy to scan
- The display script uses ANSI color codes - works best in modern terminals
