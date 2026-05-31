"""
GMP fetcher add-on for IPO Ji scraper
Fetches Grey Market Premium data from ipowatch.in
"""

import requests
from bs4 import BeautifulSoup
import re

GMP_URL = 'https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/'

def fetch_all_gmp():
    """Fetch GMP table from ipowatch.in. Returns dict of {ipo_name_lower: {'gmp': '₹13', 'gmp_value': 13, 'trend': '🟢', 'est_listing': '₹83 (18.57%)'}}"""
    result = {}
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(GMP_URL, timeout=15, headers=headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, 'html.parser')

        # Find the GMP table (first big table with IPO Name, IPO GMP columns)
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            if len(rows) < 2:
                continue

            # Check if this looks like the GMP table
            header = rows[0].get_text().lower()
            if 'gmp' not in header:
                continue

            for row in rows[1:]:
                cols = row.find_all(['td', 'th'])
                if len(cols) < 4:
                    continue

                name_cell = cols[0]
                gmp_cell = cols[1]
                trend_cell = cols[2] if len(cols) > 2 else None
                est_listing_cell = cols[4] if len(cols) > 4 else None

                # Get name
                name = name_cell.get_text(strip=True)
                name_lower = name.lower().strip()

                # Get GMP value
                gmp_text = gmp_cell.get_text(strip=True)
                gmp_match = re.search(r'[₹₹]?\s*([+-]?\d[\d,.]*)', gmp_text)
                gmp_value = float(gmp_match.group(1).replace(',', '')) if gmp_match else 0

                # Get trend
                trend = trend_cell.get_text(strip=True) if trend_cell else ''

                # Get estimated listing
                est = est_listing_cell.get_text(strip=True) if est_listing_cell else ''

                result[name_lower] = {
                    'gmp': gmp_text,
                    'gmp_value': gmp_value,
                    'trend': trend,
                    'est_listing': est,
                    'name': name,
                }

            if result:
                print(f"[GMP] Fetched GMP for {len(result)} IPOs from ipowatch.in")
                break

    except Exception as e:
        print(f"[GMP] Error fetching GMP data: {e}")

    return result


def lookup_gmp(ipo_name, gmp_data=None):
    """Look up GMP for a specific IPO name. Fuzzy matches."""
    if gmp_data is None:
        gmp_data = fetch_all_gmp()

    if not gmp_data:
        return None

    name_lower = ipo_name.lower().strip()
    # Remove "IPO", "Limited", "Ltd" for matching
    clean = re.sub(r'\b(ipo|limited|ltd|pvt)\b', '', name_lower, flags=re.I).strip()

    # Exact match first
    if name_lower in gmp_data:
        return gmp_data[name_lower]

    # Fuzzy: check if our name is contained in any GMP entry or vice versa
    for gmp_name, data in gmp_data.items():
        gmp_clean = re.sub(r'\b(ipo|limited|ltd|pvt)\b', '', gmp_name, flags=re.I).strip()
        if clean in gmp_clean or gmp_clean in clean:
            return data
        # Word overlap: if >50% of words match
        our_words = set(clean.split())
        their_words = set(gmp_clean.split())
        if len(our_words & their_words) >= max(1, len(our_words) * 0.5):
            return data

    return None


if __name__ == '__main__':
    data = fetch_all_gmp()
    print(f"\n=== All GMP data ({len(data)} entries) ===")
    for name, info in data.items():
        print(f"  {info['name']:30s} GMP={info['gmp']:8s} Value={info['gmp_value']:6.0f}")

    print("\n=== Lookup tests ===")
    for test in ['Aureate Tradde', 'M R Maniveni Foods', 'Hexagon Nutrition', 'Paytm']:
        r = lookup_gmp(test, data)
        if r:
            print(f"  {test}: GMP={r['gmp']}, Value={r['gmp_value']}")
        else:
            print(f"  {test}: NOT FOUND")
