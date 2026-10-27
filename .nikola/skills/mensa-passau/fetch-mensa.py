#!/usr/bin/env python3
"""
Fetch and parse the University of Passau Mensa menu from STWNO.
Returns structured JSON output with meals, prices, and allergen information.
"""

import json
import sys
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from html.parser import HTMLParser


class MensaMenuParser(HTMLParser):
    """Parse the HTML table response from the STWNO menu API."""
    
    def __init__(self):
        super().__init__()
        self.meals = []
        self.current_section = None
        self.current_meal = None
        self.in_section_header = False
        self.in_meal_row = False
        self.current_cell = None
        self.cell_index = 0
        self.data_buffer = []
        
    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        
        # Section header row: <tr class='gruppenkopf'>
        if tag == 'tr' and 'class' in attrs_dict:
            if attrs_dict['class'] == 'gruppenkopf':
                self.in_section_header = True
                self.current_section = None
            elif attrs_dict['class'] == 'essenzeile':
                self.in_meal_row = True
                self.cell_index = 0
                self.current_meal = {
                    'section': self.current_section or 'Other',
                    'name': '',
                    'icons': [],
                    'student_price': '',
                    'employee_price': '',
                    'guest_price': '',
                    'allergens': ''
                }
        
        # Table cells
        if tag == 'td':
            self.current_cell = 'td'
            self.data_buffer = []
            cell_class = attrs_dict.get('class', '')
            if cell_class:
                if self.in_section_header and 'gk' in cell_class:
                    self.current_cell = 'section_name'
                elif self.in_meal_row:
                    if cell_class == 'cell1':
                        self.current_cell = 'meal_name'
                    elif cell_class == 'cell2':
                        self.current_cell = 'icons'
                    elif cell_class == 'preis':
                        self.current_cell = f'price_{self.cell_index}'
        
        # Icon images in cell2
        if tag == 'img' and self.current_cell == 'icons' and 'alt' in attrs_dict:
            icon_alt = attrs_dict['alt']
            if icon_alt and self.current_meal:
                self.current_meal['icons'].append(icon_alt)
        
        # Superscript for allergens (in meal name)
        if tag == 'sup' and self.current_cell == 'meal_name':
            self.current_cell = 'allergens'
    
    def handle_endtag(self, tag):
        if tag == 'tr':
            if self.in_section_header:
                self.in_section_header = False
            elif self.in_meal_row:
                self.in_meal_row = False
                # Save the completed meal
                if self.current_meal and self.current_meal['name']:
                    self.meals.append(self.current_meal)
                self.current_meal = None
        
        if tag == 'td':
            data = ''.join(self.data_buffer).strip()
            
            if self.current_cell == 'section_name':
                self.current_section = data
            elif self.current_cell == 'meal_name' and self.current_meal:
                self.current_meal['name'] = data
            elif self.current_cell == 'allergens' and self.current_meal:
                self.current_meal['allergens'] = data
            elif self.current_cell and self.current_cell.startswith('price_') and self.current_meal:
                # Prices are in order: student, employee, guest
                if self.cell_index == 0:
                    self.current_meal['student_price'] = data
                elif self.cell_index == 1:
                    self.current_meal['employee_price'] = data
                elif self.cell_index == 2:
                    self.current_meal['guest_price'] = data
                self.cell_index += 1
            
            self.current_cell = None
            self.data_buffer = []
        
        if tag == 'sup':
            if self.current_cell == 'allergens':
                self.current_cell = 'meal_name'
    
    def handle_data(self, data):
        if self.current_cell:
            self.data_buffer.append(data)


def fetch_mensa_menu(location_id='UNI-P', date=None):
    """
    Fetch the mensa menu from STWNO API.
    
    Args:
        location_id: Location identifier (default: UNI-P for Uni Passau)
        date: Date in YYYY-MM-DD format (default: today)
    
    Returns:
        List of meal dictionaries
    """
    if date is None:
        # Use Europe/Berlin timezone for "today"
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo('Europe/Berlin')
            date = datetime.now(tz).strftime('%Y-%m-%d')
        except ImportError:
            # Fallback: use UTC (close enough for menu purposes)
            date = datetime.utcnow().strftime('%Y-%m-%d')
    
    url = 'https://stwno.de/infomax/daten-extern/html/speiseplan-render.php'
    
    # Prepare POST data
    data = {
        'func': 'make_spl',
        'locId': location_id,
        'lang': 'de',
        'date': date,
        'w': ''
    }
    
    # Prepare request
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': 'https://stwno.de',
        'Referer': f'https://stwno.de/infomax/daten-extern/html/speiseplaene.php?einrichtung={location_id}',
        'User-Agent': 'Mozilla/5.0 (compatible; BobMensaSkill/1.0)',
        'X-Request': 'JSON',
        'X-Requested-With': 'XMLHttpRequest'
    }
    
    # Make request
    encoded_data = urlencode(data).encode('utf-8')
    request = Request(url, data=encoded_data, headers=headers)
    
    try:
        with urlopen(request, timeout=10) as response:
            html_content = response.read().decode('utf-8')
            
            # Parse the HTML
            parser = MensaMenuParser()
            parser.feed(html_content)
            
            return parser.meals
    
    except Exception as e:
        raise Exception(f"Failed to fetch mensa menu: {str(e)}")


def main():
    """Main entry point."""
    try:
        meals = fetch_mensa_menu()
        
        # Output as JSON
        print(json.dumps({
            'success': True,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'meals': meals
        }, indent=2, ensure_ascii=False))
        
        return 0
    
    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': str(e)
        }, indent=2), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
