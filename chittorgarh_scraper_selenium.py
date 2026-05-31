"""
Chittorgarh.com IPO Scraper - Selenium Version
Handles JavaScript-rendered content for reliable IPO extraction
"""

import requests
from bs4 import BeautifulSoup
import re
import time
from datetime import datetime

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    print("[Warning] Selenium not installed. Install with: pip install selenium")

class ChittorgarhScraperSelenium:
    BASE_URL = 'https://www.chittorgarh.com'

    def __init__(self):
        self.driver = None
        self.selenium_enabled = SELENIUM_AVAILABLE

    def _init_driver(self):
        """Initialize Selenium WebDriver"""
        if not self.selenium_enabled:
            print("[Selenium] Not available. Install: pip install selenium webdriver-manager")
            return False

        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.chrome.service import Service

            print("[Selenium] Initializing Chrome WebDriver...")

            chrome_options = Options()
            chrome_options.add_argument('--start-maximized')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')

            # Try headless mode (no visible browser window)
            chrome_options.add_argument('--headless')

            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            print("[Selenium] ✅ WebDriver initialized successfully")
            return True

        except Exception as e:
            print(f"[Selenium] ❌ Error initializing driver: {e}")
            print("[Selenium] Install dependencies: pip install selenium webdriver-manager")
            self.selenium_enabled = False
            return False

    def _close_driver(self):
        """Close Selenium WebDriver"""
        if self.driver:
            try:
                self.driver.quit()
                self.driver = None
            except:
                pass

    def search_ipos(self, query):
        """
        Search for IPOs on Chittorgarh using Selenium.
        Returns list of matching IPOs with basic info.
        """
        results = []
        query_lower = query.lower()

        if not self._init_driver():
            print("[Chittorgarh Search] Selenium not available, falling back to requests")
            return self._search_ipos_fallback(query)

        try:
            # Try main IPO list page
            urls = [
                f'{self.BASE_URL}/report/ipo-in-india-list-main-board-sme/82/',
                f'{self.BASE_URL}/report/ipo-subscription-status-live-bidding-data-bse-nse/21/',
            ]

            for list_url in urls:
                try:
                    print(f"[Chittorgarh Search] Loading page with Selenium: {list_url[:50]}...")
                    self.driver.get(list_url)

                    # Wait for table to load
                    try:
                        WebDriverWait(self.driver, 10).until(
                            EC.presence_of_all_elements_located((By.TAG_NAME, "table"))
                        )
                    except:
                        print(f"[Chittorgarh Search] Table not found within timeout")
                        continue

                    # Wait a bit more for JavaScript to render
                    time.sleep(2)

                    # Parse with BeautifulSoup after JavaScript rendering
                    soup = BeautifulSoup(self.driver.page_source, 'html.parser')

                    # Look for all tables
                    tables = soup.find_all('table')
                    print(f"[Chittorgarh Search] Found {len(tables)} table(s)")

                    seen = set()

                    for table_idx, table in enumerate(tables):
                        rows = table.find_all('tr')

                        for row in rows[1:]:  # Skip header
                            cols = row.find_all(['td', 'th'])
                            if len(cols) < 2:
                                continue

                            # First column usually has IPO name
                            name_cell = cols[0]
                            name_link = name_cell.find('a')

                            if name_link:
                                name = name_link.get_text(strip=True)
                                href = name_link.get('href', '')
                            else:
                                name = name_cell.get_text(strip=True)
                                href = ''

                            # Clean up name
                            name = re.sub(r'\s+(SME\s+)?IPO\s*$', '', name, flags=re.IGNORECASE).strip()
                            name = re.sub(r'\s+Rights\s+Issue\s*$', '', name, flags=re.IGNORECASE).strip()
                            name = ' '.join(name.split())

                            # Check if matches query
                            if query_lower in name.lower() and name.lower() not in seen:
                                seen.add(name.lower())

                                # Extract columns
                                status = cols[1].get_text(strip=True) if len(cols) > 1 else 'TBA'
                                open_date = cols[2].get_text(strip=True) if len(cols) > 2 else 'TBA'
                                close_date = cols[3].get_text(strip=True) if len(cols) > 3 else 'TBA'

                                # Construct full URL
                                if href:
                                    full_url = href if href.startswith('http') else self.BASE_URL + href
                                else:
                                    slug = name.lower().replace(' ', '-')
                                    full_url = f'{self.BASE_URL}/ipo/{slug}-ipo/'

                                results.append({
                                    'name': name,
                                    'url': full_url,
                                    'status': status,
                                    'open_date': open_date,
                                    'close_date': close_date,
                                    'source': 'Chittorgarh (Selenium)',
                                })

                                print(f"[Chittorgarh Search] ✅ Found: {name}")

                except Exception as e:
                    print(f"[Chittorgarh Search] Error scraping {list_url[:50]}...: {e}")
                    continue

            print(f"[Chittorgarh Search] Total found: {len(results)} IPO(s) matching '{query}'")

        except Exception as e:
            print(f"[Chittorgarh Search] Fatal error: {e}")

        finally:
            self._close_driver()

        return results

    def _search_ipos_fallback(self, query):
        """Fallback search using requests (no Selenium)"""
        print(f"[Chittorgarh Search] Fallback: Using requests (no JavaScript support)")
        results = []
        query_lower = query.lower()

        try:
            url = f'{self.BASE_URL}/report/ipo-in-india-list-main-board-sme/82/'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            resp = requests.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(resp.content, 'html.parser')

            seen = set()
            for table in soup.find_all('table'):
                for row in table.find_all('tr')[1:]:
                    cols = row.find_all(['td', 'th'])
                    if len(cols) < 2:
                        continue

                    name = cols[0].get_text(strip=True)
                    name = re.sub(r'\s+(SME\s+)?IPO\s*$', '', name, flags=re.IGNORECASE).strip()
                    name = ' '.join(name.split())

                    if query_lower in name.lower() and name.lower() not in seen:
                        seen.add(name.lower())
                        results.append({
                            'name': name,
                            'url': f'{self.BASE_URL}/ipo/{name.lower().replace(" ", "-")}-ipo/',
                            'status': cols[1].get_text(strip=True) if len(cols) > 1 else 'TBA',
                            'open_date': cols[2].get_text(strip=True) if len(cols) > 2 else 'TBA',
                            'close_date': cols[3].get_text(strip=True) if len(cols) > 3 else 'TBA',
                            'source': 'Chittorgarh (Fallback)',
                        })

        except Exception as e:
            print(f"[Chittorgarh Search] Fallback error: {e}")

        return results

    def scrape_ipo_details(self, ipo_url):
        """
        Scrape detailed information from an IPO detail page using requests + BeautifulSoup.
        """
        details = {
            'url': ipo_url,
            'scraped_at': datetime.now().isoformat(),
            'error': None,
        }

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            resp = requests.get(ipo_url, headers=headers, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, 'html.parser')

            # Extract company name - clean up common suffixes
            title = soup.find('h1') or soup.find('title')
            if title:
                name = title.get_text(strip=True)
                # Remove "IPO Details", "SME", etc.
                name = re.sub(r'\s+(IPO\s+)?Details\s*$', '', name, flags=re.IGNORECASE)
                name = re.sub(r'\s+(IPO|SME|Rights Issue)\s*$', '', name, flags=re.IGNORECASE)
                details['name'] = name.strip()

            # Try to extract sector from page text (SME IPOs are common)
            page_text = soup.get_text().upper()
            if 'SME' in page_text and 'sector' not in details:
                details['sector'] = 'SME'

            # Find all tables and extract data with flexible field matching
            for table in soup.find_all('table'):
                rows = table.find_all('tr')
                for row in rows:
                    cols = row.find_all(['td', 'th'])
                    if len(cols) >= 2:
                        key = cols[0].get_text(strip=True).lower()
                        val = cols[1].get_text(strip=True)

                        # Skip empty or TBA values but allow numeric values
                        if not val or val == '-':
                            continue

                        # More flexible field matching to handle variations
                        if 'company name' in key or 'issuer name' in key:
                            details['company_name'] = val
                        elif 'subscription status' in key or 'ipo status' in key:
                            details['status'] = val
                        elif key == 'status':
                            details['status'] = val
                        elif 'issue size' in key or 'offered shares' in key:
                            details['issue_size'] = val
                        elif 'price band' in key or 'issue price' in key or 'price range' in key:
                            details['price_band'] = val
                        elif 'subscription opens' in key or 'opens on' in key or 'open date' in key:
                            details['open_date'] = val
                        elif 'subscription closes' in key or 'closes on' in key or 'close date' in key:
                            details['close_date'] = val
                        elif 'listing date' in key or 'listed on' in key:
                            details['listing_date'] = val
                        elif 'listing price' in key or 'delist price' in key:
                            details['listing_price'] = val
                        elif 'qib' in key and 'subscription' in key:
                            details['qib_subscription'] = val
                        elif ('hni' in key or 'nii' in key) and 'subscription' in key:
                            details['hni_subscription'] = val
                        elif 'retail' in key and 'subscription' in key:
                            details['retail_subscription'] = val
                        elif 'gmp' in key or 'grey market' in key:
                            details['gmp'] = val
                        elif 'sector' in key or 'industry' in key or 'category' in key:
                            if 'sector' not in details or details['sector'] == 'SME':
                                details['sector'] = val
                        elif 'isin' in key:
                            details['isin'] = val

            # Set defaults for missing critical fields
            if 'status' not in details or not details['status']:
                details['status'] = 'TBA'
            if 'sector' not in details or not details['sector']:
                details['sector'] = 'SME'  # Default to SME if not found

            # Log what was extracted
            extracted_fields = [k for k in details.keys() if details[k] and k not in ['url', 'scraped_at', 'error']]
            print(f"[Chittorgarh Detail] ✅ Scraped: {details.get('name', 'Unknown')}")
            print(f"[Chittorgarh Detail] Fields found: {', '.join(extracted_fields)}")

        except Exception as e:
            print(f"[Chittorgarh Detail] ❌ Error: {e}")
            details['error'] = str(e)

        return details


# Singleton instance
scraper = ChittorgarhScraperSelenium()
