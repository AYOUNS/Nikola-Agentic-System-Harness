---
name: mensa-passau
description: Use when the user asks about the Passau mensa, cafeteria, canteen, or what's for lunch today. Fetches and displays today's menu from the University of Passau cafeteria.
---

# Mensa Passau

This skill fetches and displays today's menu from the University of Passau cafeteria (Mensa).

## Steps

1. **Run the fetch script** using `execute_command`:
   ```
   python3 .nikola/skills/mensa-passau/fetch-mensa.py
   ```
   
   The script will:
   - Fetch today's menu from the STWNO API
   - Parse the HTML response
   - Extract all meals with their sections, names, prices, and allergen information
   - Return structured JSON output

2. **Check the script result:**
   - If the script fails or returns an error, explain to the user that the mensa menu could not be fetched and show any error message
   - If successful, proceed to step 3

3. **Display the menu** in a clear, readable format:
   - Group meals by section (Suppen, Hauptgerichte, Beilagen, etc.)
   - For each meal show:
     - Meal name (with allergen codes if present in parentheses)
     - Dietary icons (vegetarisch, Vegan, mensaVital, etc.)
     - Prices: Student / Employee / Guest
     - Allergen/additive information
   - Use a clean markdown format with headings and bullet points
   - If no meals are available (e.g., weekend, holiday), inform the user

4. **Add context:** Briefly mention that this is today's menu from the University of Passau Mensa

## Notes

- The script defaults to today's date in Europe/Berlin timezone
- The API endpoint is: `https://stwno.de/infomax/daten-extern/html/speiseplan-render.php`
- Location ID is hardcoded to `UNI-P` (University of Passau)
