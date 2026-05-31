"""
IPO Ji Scraper
Extracts complete IPO data from ipoji.com for v1.1 scoring framework
Primary data source (Chittorgarh as fallback)
"""

import requests
from bs4 import BeautifulSoup
import re
import time
from datetime import datetime
import json

class IPOJiScraper:
    BASE_URL = 'https://www.ipoji.com'
    RATE_LIMIT_SECONDS = 5  # 5 seconds between detail scrapes
    last_request_time = 0

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def _respect_rate_limit(self):
        """Enforce rate limiting between requests"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.RATE_LIMIT_SECONDS:
            wait_time = self.RATE_LIMIT_SECONDS - elapsed
            print(f"[IPO Ji] Rate limit: waiting {wait_time:.0f}s")
            time.sleep(wait_time)
        self.last_request_time = time.time()

    def search_ipos(self, query):
        """Search for IPOs on IPO Ji website. Returns list of matching IPOs with URLs."""
        results = []
        query_lower = query.lower()
        print(f"[IPO Ji Search] Searching for: {query}")

        try:
            search_urls = [
                f"{self.BASE_URL}/?s={query}&post_type=ipo",
                f"{self.BASE_URL}/ipo-list/",
                f"{self.BASE_URL}/upcoming-ipo/",
            ]

            for search_url in search_urls:
                print(f"[IPO Ji Search] Trying: {search_url}")
                try:
                    resp = self.session.get(search_url, timeout=15)
                    resp.raise_for_status()
                    soup = BeautifulSoup(resp.content, 'html.parser')
                    page_results = []

                    articles = soup.find_all('article')
                    if articles:
                        print(f"[IPO Ji Search] Found {len(articles)} articles")
                        page_results.extend(self._parse_articles(articles, query_lower))

                    if not page_results:
                        divs = soup.find_all('div', class_=re.compile(r'ipo|post|item', re.I))
                        if divs:
                            print(f"[IPO Ji Search] Found {len(divs)} divs with IPO classes")
                            page_results.extend(self._parse_divs(divs, query_lower))

                    if not page_results:
                        links = soup.find_all('a', href=True)
                        ipo_links = [l for l in links if any(x in l.get_text().lower() for x in ['ipo', 'listing', 'offering'])]
                        if ipo_links:
                            print(f"[IPO Ji Search] Found {len(ipo_links)} IPO-related links")
                            page_results.extend(self._parse_links(ipo_links, query_lower))

                    results.extend(page_results)
                    if page_results:
                        print(f"[IPO Ji Search] Found {len(page_results)} IPO(s) on this page")
                        return results

                except Exception as e:
                    print(f"[IPO Ji Search] Error with {search_url}: {e}")
                    continue

            if not results:
                print(f"[IPO Ji Search] No results found for: {query}")
            return results

        except Exception as e:
            print(f"[IPO Ji Search] Error: {e}")
            import traceback
            traceback.print_exc()
            return []

    # --- Navigation filtering ---

    NAV_BLACKLIST = [
        'current ipo', 'upcoming ipo', 'ipo calendar', 'ipo list',
        'live & open', 'sme ipo', 'ipo events', 'ipo news',
        'about', 'contact', 'home', 'menu', 'login', 'register',
        'view all', 'see all', 'show all', 'more ipo', 'all ipo',
    ]

    NAV_REGEX = re.compile(
        r'(^view\s|->|<-|>>|<<|click here|read more|see more|show more|view details)',
        re.IGNORECASE
    )

    def _is_nav_element(self, name_lower):
        """Check if name looks like a navigation/section heading, not an actual IPO"""
        clean = re.sub(r'\b(ipo|sme|live|open|current|upcoming)\b', '', name_lower).strip()
        if len(clean) < 2:
            return True
        for pattern in self.NAV_BLACKLIST:
            if pattern in name_lower and len(name_lower) < 50:
                return True
        if self.NAV_REGEX.search(name_lower):
            return True
        if any(c in name_lower for c in '→←»«►▶'):
            return True
        return False

    @staticmethod
    def _clean_name(raw_name):
        """Clean up extracted IPO name"""
        name = raw_name.strip()
        name = re.sub(r'^View(?=[A-Z]|\s)', '', name).strip()
        name = re.sub(r'\s+(IPO|SME)\s*$', '', name, flags=re.IGNORECASE).strip()
        name = re.sub(r'[→←»«►▶]+$', '', name).strip()
        return name

    # --- Parsers ---

    def _parse_articles(self, articles, query_lower):
        results = []
        for article in articles:
            try:
                heading = article.find(['h1', 'h2', 'h3', 'h4'])
                if not heading:
                    continue
                raw_name = heading.get_text(strip=True)
                raw_name_lower = raw_name.lower()
                if self._is_nav_element(raw_name_lower):
                    continue
                name = self._clean_name(raw_name)
                link = article.find('a', href=True)
                if not link:
                    continue
                url = link.get('href', '')
                if not url.startswith('http'):
                    url = self.BASE_URL + url
                if query_lower in raw_name_lower or query_lower in name.lower():
                    results.append({'name': name, 'url': url, 'source': 'ipoji', 'status': 'TBA'})
                    print(f"[IPO Ji Search] Found: {name}")
            except Exception as e:
                continue
        return results

    def _parse_divs(self, divs, query_lower):
        results = []
        sample_names = []
        for idx, div in enumerate(divs):
            try:
                heading = div.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a'])
                if not heading:
                    continue
                raw_name = heading.get_text(strip=True)
                if not raw_name or len(raw_name) < 3:
                    continue
                if len(sample_names) < 5:
                    sample_names.append(raw_name)
                raw_name_lower = raw_name.lower()
                if self._is_nav_element(raw_name_lower):
                    continue
                name = self._clean_name(raw_name)
                link = div.find('a', href=True) or (heading if heading.name == 'a' else None)
                if not link:
                    continue
                url = link.get('href', '')
                if not url.startswith('http') and url:
                    url = self.BASE_URL + url
                if url and (query_lower in raw_name_lower or query_lower in name.lower()):
                    results.append({'name': name, 'url': url, 'source': 'ipoji', 'status': 'TBA'})
                    if len(results) <= 10:
                        print(f"[IPO Ji Search] Div Found: {name}")
            except Exception as e:
                continue
        if sample_names and not results:
            print(f"[IPO Ji Search] Sample text from divs: {sample_names[:3]}")
        return results

    def _parse_links(self, links, query_lower):
        results = []
        sample_names = []
        for link in links[:100]:
            try:
                raw_name = link.get_text(strip=True)
                if not raw_name or len(raw_name) < 3:
                    continue
                if len(sample_names) < 5:
                    sample_names.append(raw_name)
                raw_name_lower = raw_name.lower()
                if self._is_nav_element(raw_name_lower):
                    continue
                name = self._clean_name(raw_name)
                url = link.get('href', '')
                if not url.startswith('http') and url:
                    url = self.BASE_URL + url
                if query_lower in raw_name_lower or query_lower in name.lower():
                    results.append({'name': name, 'url': url, 'source': 'ipoji', 'status': 'TBA'})
                    if len(results) <= 10:
                        print(f"[IPO Ji Search] Link Found: {name}")
            except Exception as e:
                continue
        if sample_names and not results:
            print(f"[IPO Ji Search] Sample text from links: {sample_names[:3]}")
        return results

    # --- Detail scraping ---

    def scrape_ipo_details(self, url):
        """Scrape detailed IPO information from IPO Ji page.
        
        IPO Ji detail pages use a mix of:
        1. Key-value text blocks (e.g., "Issue price\\n51-52 per equity share")
        2. Subscription summary blocks (e.g., "QIB 1.00x")
        3. HTML tables for lot sizes, reservation, financials
        4. Timeline sections for dates
        
        We extract from ALL of these sources.
        """
        details = {
            'url': url,
            'source': 'ipoji',
            'scraped_at': datetime.now().isoformat(),
            'error': None,
        }

        print(f"[IPO Ji Details] Scraping: {url}")

        try:
            self._respect_rate_limit()
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, 'html.parser')

            # Get full page text for regex extraction
            page_text = soup.get_text(separator='\n')
            page_text_upper = page_text.upper()

            # ---- STEP 1: Extract Name from h1 ----
            h1 = soup.find('h1')
            if h1:
                name = h1.get_text(strip=True)
                name = re.sub(r'\s*(IPO|SME IPO|Details)\s*$', '', name, flags=re.IGNORECASE).strip()
                details['name'] = name
                print(f"[IPO Ji Details] Name: {name}")

            # ---- STEP 2: Extract from meta tags (most reliable) ----
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc:
                desc = meta_desc.get('content', '')
                # "priced at 51-52 with 27.04 crore issue size. Opens May 22, 2026-May 26, 2026"
                price_m = re.search(r'priced at\s*(?:₹|Rs\.?)?\s*([\d,]+)\s*(?:to|–|-)\s*(?:₹|Rs\.?)?\s*([\d,]+)', desc)
                if price_m:
                    details['price_band'] = f"{price_m.group(1)}-{price_m.group(2)}"
                    details['issue_price'] = price_m.group(2).replace(',', '')
                    print(f"[IPO Ji Details] Price (meta): {details['price_band']}")

                size_m = re.search(r'(?:₹|Rs\.?)?\s*([\d,.]+)\s*(?:crore|Cr)', desc, re.I)
                if size_m:
                    details['issue_size'] = f"{size_m.group(1)} Cr"
                    details['issue_size_cr'] = size_m.group(1).replace(',', '')
                    print(f"[IPO Ji Details] Issue Size (meta): {details['issue_size']}")

                dates_m = re.search(r'Opens?\s+(\w+\s+\d{1,2},?\s+\d{4})\s*(?:to|–|-)\s*(\w+\s+\d{1,2},?\s+\d{4})', desc, re.I)
                if dates_m:
                    details['open_date'] = dates_m.group(1)
                    details['close_date'] = dates_m.group(2)
                    print(f"[IPO Ji Details] Dates (meta): {details['open_date']} - {details['close_date']}")

            # ---- STEP 3: Extract from page text key-value patterns ----
            # IPO Ji uses patterns like "Price band\n51-52 per equity share"
            kv_patterns = [
                # Price band
                (r'(?:Price\s*band|Offer\s*Price|Issue\s*price)\s*\n?\s*(?:₹|Rs\.?)?\s*([\d,]+(?:\s*-\s*[\d,]+)?)\s*(?:per)?', 'price_band'),
                # Issue size
                (r'Issue\s*size\s*\n?\s*(?:₹|Rs\.?)?\s*([\d,.]+\s*Cr)', 'issue_size'),
                # Lot size
                (r'Lot\s*size\s*\n?\s*(\d[\d,]*)', 'lot_size'),
                # Open date
                (r'(?:Open\s*Date|IPO\s*Dates?)\s*\n?\s*(\w+\s+\d{1,2},?\s+\d{4})', 'open_date'),
                # Close date
                (r'Close\s*Date\s*\n?\s*(\w+\s+\d{1,2},?\s+\d{4})', 'close_date'),
                # Listing date
                (r'Listing(?:\s*Date)?\s*\n?\s*(\w+\s+\d{1,2},?\s+\d{4})', 'listing_date'),
                # Allotment date
                (r'Allotment\s*Date\s*\n?\s*(\w+\s+\d{1,2},?\s+\d{4})', 'allotment_date'),
            ]

            for pattern, field in kv_patterns:
                if field not in details or not details[field]:
                    m = re.search(pattern, page_text, re.IGNORECASE)
                    if m:
                        val = m.group(1).strip()
                        if val and val != '-':
                            details[field] = val
                            print(f"[IPO Ji Details] {field} (text): {val}")

            # ---- STEP 4: Extract subscription data ----
            # IPO Ji shows "QIB 1.00x", "Individual 2.09x", "NIIs 2.23x", "Total 1.81x"
            sub_patterns = [
                (r'QIB\s+([\d.]+)\s*x', 'qib_subscription'),
                (r'(?:NII|HNI)s?\s+([\d.]+)\s*x', 'hni_subscription'),
                (r'bHNI\s+([\d.]+)\s*x', 'bhni_subscription'),
                (r'sHNI\s+([\d.]+)\s*x', 'shni_subscription'),
                (r'(?:Individual|Retail)\s+([\d.]+)\s*x', 'retail_subscription'),
                (r'Total\s+([\d.]+)\s*x', 'total_subscription'),
            ]

            for pattern, field in sub_patterns:
                m = re.search(pattern, page_text, re.IGNORECASE)
                if m:
                    val = float(m.group(1))
                    details[field] = val
                    print(f"[IPO Ji Details] {field}: {val}x")

            # If we got NII but not HNI, use NII as HNI
            if 'hni_subscription' not in details and 'bhni_subscription' in details:
                details['hni_subscription'] = details['bhni_subscription']

            # ---- STEP 5: Extract GMP ----
            gmp_patterns = [
                r'(?:GMP|Grey\s*Market\s*Premium)\s*(?:is\s*)?(?:₹|Rs\.?)?\s*([+-]?\d[\d,.]*)\s*%?',
                r'(?:GMP|Premium)\s*[:\s]*([+-]?\d[\d,.]*)\s*%',
                r'expected\s*(?:premium|GMP)\s*(?:of\s*)?(?:₹|Rs\.?)?\s*([+-]?\d[\d,.]*)',
            ]
            for pattern in gmp_patterns:
                m = re.search(pattern, page_text, re.IGNORECASE)
                if m:
                    details['gmp'] = f"{m.group(1)}%"
                    print(f"[IPO Ji Details] GMP: {details['gmp']}")
                    break

            # ---- STEP 6: Extract from HTML tables ----
            tables = soup.find_all('table')
            print(f"[IPO Ji Details] Found {len(tables)} table(s)")
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    cols = row.find_all(['td', 'th'])
                    if len(cols) < 2:
                        continue
                    key = cols[0].get_text(strip=True).lower()
                    val = cols[1].get_text(strip=True)
                    if not val or val == '-' or val == 'TBA':
                        continue

                    # Subscription table: "Day 3 ... QIB ... NII ... Retail ... Total"
                    # Already handled by regex above, but capture table-specific data
                    if 'qib' in key and ('subscription' in key or 'x' in val.lower()):
                        m = re.search(r'([\d.]+)', val)
                        if m and 'qib_subscription' not in details:
                            details['qib_subscription'] = float(m.group(1))

                    elif ('hni' in key or 'nii' in key) and ('subscription' in key or 'x' in val.lower()):
                        m = re.search(r'([\d.]+)', val)
                        if m and 'hni_subscription' not in details:
                            details['hni_subscription'] = float(m.group(1))

                    elif 'retail' in key and ('subscription' in key or 'x' in val.lower()):
                        m = re.search(r'([\d.]+)', val)
                        if m and 'retail_subscription' not in details:
                            details['retail_subscription'] = float(m.group(1))

                    elif 'issue size' in key:
                        if 'issue_size' not in details:
                            details['issue_size'] = val
                            m = re.search(r'([\d,.]+)\s*(?:Cr|crore)', val, re.I)
                            if m:
                                details['issue_size_cr'] = m.group(1).replace(',', '')

                    elif 'price' in key and 'band' in key:
                        if 'price_band' not in details:
                            details['price_band'] = val

                    elif 'sector' in key or 'industry' in key:
                        if 'sector' not in details:
                            details['sector'] = val

                    elif 'listing price' in key:
                        m = re.search(r'[\d,.]+', val)
                        if m:
                            details['listing_price'] = float(m.group(0).replace(',', ''))

                    elif 'current price' in key or 'cmp' in key:
                        m = re.search(r'[\d,.]+', val)
                        if m:
                            details['current_price'] = float(m.group(0).replace(',', ''))

            # ---- STEP 7: Extract valuations ----
            val_patterns = [
                (r'P/E\s*Post\s*IPO.*?\n\s*([\d.]+)', 'pe_ratio'),
                (r'RoNW[^)]*\)\s*\n?\s*([\d.,]+)\s*%', 'ronw'),
                (r'ROCE[^)]*\)\s*\n?\s*([\d.,]+)\s*%', 'roce'),
                (r'Debt\s*/\s*Equity[^)]*\)\s*\n?\s*([\d.]+)', 'debt_equity'),
                (r'Market\s*Cap[^)]*\)\s*\n?\s*(?:₹|Rs\.?)?\s*([\d,.]+)\s*Cr', 'market_cap'),
            ]
            for pattern, field in val_patterns:
                m = re.search(pattern, page_text, re.IGNORECASE)
                if m:
                    details[field] = m.group(1).replace(',', '')
                    print(f"[IPO Ji Details] {field}: {details[field]}")

            # ---- STEP 8: Infer status ----
            if 'status' not in details or not details.get('status'):
                if 'ALLOTMENT OUT' in page_text_upper or 'ALLOTMENT DATE' in page_text_upper:
                    details['status'] = 'Allotment Out'
                elif 'LISTED' in page_text_upper and 'LISTING DATE' in page_text_upper:
                    # Check if listing date is in the past
                    details['status'] = 'Listed'
                elif 'CLOSED' in page_text_upper or 'SUBSCRIPTION CLOSED' in page_text_upper:
                    details['status'] = 'Closed'
                elif 'OPEN' in page_text_upper or 'ONGOING' in page_text_upper:
                    details['status'] = 'Open'
                elif 'UPCOMING' in page_text_upper:
                    details['status'] = 'Upcoming'
                else:
                    details['status'] = 'TBA'

            # Also check the badge text near the h1
            badge = soup.find(string=re.compile(r'Allotment Out|Open|Closed|Listed|Upcoming', re.I))
            if badge:
                badge_text = badge.strip()
                if 'allotment' in badge_text.lower():
                    details['status'] = 'Allotment Out'
                elif 'listed' == badge_text.lower().strip():
                    details['status'] = 'Listed'

            # ---- STEP 9: Infer sector ----
            if 'sector' not in details or not details.get('sector'):
                if 'BSE SME' in page_text or 'SME' in page_text_upper:
                    details['sector'] = 'SME'
                elif 'NSE' in page_text:
                    details['sector'] = 'Mainboard'
                else:
                    details['sector'] = 'TBA'

            # ---- Summary ----
            print(f"[IPO Ji Details] === EXTRACTION SUMMARY ===")
            for k, v in sorted(details.items()):
                if v and k not in ['url', 'scraped_at', 'error', 'source']:
                    print(f"[IPO Ji Details]   {k}: {v}")
            extracted = [k for k in details if details[k] and k not in ['url', 'scraped_at', 'error', 'source']]
            print(f"[IPO Ji Details] Total fields: {len(extracted)}")

        except Exception as e:
            print(f"[IPO Ji Details] Error: {e}")
            import traceback
            traceback.print_exc()
            details['error'] = str(e)

        return details

    def get_listing_price(self, ipo_name):
        """Get current listing price for an IPO"""
        try:
            results = self.search_ipos(ipo_name)
            if not results:
                return None
            details = self.scrape_ipo_details(results[0]['url'])
            if details.get('current_price'):
                return details['current_price']
            elif details.get('listing_price'):
                return details['listing_price']
            return None
        except Exception as e:
            print(f"[IPO Ji] Error getting listing price: {e}")
            return None

    def calculate_listing_gain(self, issue_price, current_price):
        """Calculate listing day gain percentage"""
        if not issue_price or not current_price:
            return None
        try:
            return round(((current_price - issue_price) / issue_price) * 100, 2)
        except:
            return None


# Singleton instance
scraper = IPOJiScraper()
