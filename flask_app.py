"""
IPO Analyzer Flask API
Real-time IPO search via live internet sources only.
No database, no curated list — 100% live data.

Search sources (per query):
  1. Yahoo Finance autocomplete  — fast (~2s), any Indian company by name
  2. Chittorgarh IPO cache       — current + upcoming IPOs with dates/price band
                                   (background-loaded on startup, refreshed every 30 min)
  3. NSE India API               — current subscription data (session-based)
"""

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import os, json, threading, time
from datetime import datetime, timedelta
import sqlite3
from ipoji_scraper import scraper as ipoji_scraper
from chittorgarh_scraper_selenium import scraper as chittorgarh_scraper
from gmp_fetcher import fetch_all_gmp, lookup_gmp
from email_notifier import check_and_alert, get_decision, is_configured as email_configured

app = Flask(__name__)
CORS(app)

@app.route('/')
def serve_frontend():
    """Serve the dashboard HTML"""
    frontend_path = os.path.join(os.path.dirname(__file__), 'frontend', 'ipo_dashboard_v2.html')
    if os.path.exists(frontend_path):
        return send_file(frontend_path)
    return '<h1>IPO Analyzer API</h1><p>Frontend not found. Check /api/health</p>'


DB_PATH    = os.path.join(os.path.dirname(__file__), 'data', 'ipo_analyzer.db')
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config', 'settings.json')


# ─────────────────────────────────────────────────────────────────────────────
# Background IPO cache  (Chittorgarh + NSE — filled once at startup)
# ─────────────────────────────────────────────────────────────────────────────
_cache_lock   = threading.Lock()
_ipo_cache    = {'data': [], 'loaded_at': None}
CACHE_TTL_SEC = 1800   # 30 min


def _cache_age_seconds():
    if not _ipo_cache['loaded_at']:
        return None
    return (datetime.now() - _ipo_cache['loaded_at']).total_seconds()


def _cache_stale():
    age = _cache_age_seconds()
    return age is None or age > CACHE_TTL_SEC


def _load_cache():
    """Fetch IPO list from web sources and store in _ipo_cache. Runs in background."""
    print("[CACHE] Loading live IPO list from internet …")
    fresh = []

    # ── Chittorgarh: DISABLED (404 URLs) ───────────────────────────────────
    # The old Chittorgarh URLs are no longer accessible.
    # Focusing on: Yahoo Finance (live) + NSE India (cache) + IPOAlerts (cache)
    print("[CACHE] Chittorgarh: SKIPPED (URLs changed)")

    # # Uncomment if Chittorgarh URLs are fixed
    # try:
    #     fresh += _scrape_chittorgarh(
    #         'https://www.chittorgarh.com/report/ipo-subscription-status-live-data/',
    #         label='Open'
    #     )
    #     print(f"[CACHE] Chittorgarh Open: {len(fresh)} entries")
    # except Exception as e:
    #     print(f"[CACHE] Chittorgarh Open error: {e}")

    # ── NSE India: current allotment / subscription ──────────────────────────
    try:
        nse = _fetch_nse_ipos()
        for item in nse:
            if not _name_dup(item['name'], fresh):
                fresh.append(item)
        print(f"[CACHE] NSE added, total: {len(fresh)}")
    except Exception as e:
        print(f"[CACHE] NSE error: {e}")

    # ── IPOAlerts: comprehensive NSE/BSE IPO aggregator ─────────────────────
    try:
        ipoalerts_data = _fetch_ipoalerts()
        for item in ipoalerts_data:
            if not _name_dup(item['name'], fresh):
                fresh.append(item)
        print(f"[CACHE] IPOAlerts added, total: {len(fresh)}")
    except Exception as e:
        print(f"[CACHE] IPOAlerts error: {e}")

    # ── NSE-BSE API: open-source npm package with IPO info ─────────────────
    try:
        nse_bse_data = _fetch_nse_bse_api()
        for item in nse_bse_data:
            if not _name_dup(item['name'], fresh):
                fresh.append(item)
        print(f"[CACHE] NSE-BSE API added, total: {len(fresh)}")
    except Exception as e:
        print(f"[CACHE] NSE-BSE API error: {e}")

    # ── NSE Official: direct scrape from NSE website ─────────────────────────
    try:
        nse_official_data = _fetch_nse_ipos_official()
        for item in nse_official_data:
            if not _name_dup(item['name'], fresh):
                fresh.append(item)
        print(f"[CACHE] NSE Official added, total: {len(fresh)}")
    except Exception as e:
        print(f"[CACHE] NSE Official error: {e}")

    with _cache_lock:
        _ipo_cache['data']      = fresh
        _ipo_cache['loaded_at'] = datetime.now()

    print(f"[CACHE] Done — {len(fresh)} IPOs cached from internet.")


def _scrape_chittorgarh(url, label=''):
    """
    Extract IPO names from Chittorgarh by finding IPO detail-page links.
    Chittorgarh IPO detail URLs follow this stable pattern:
        /ipo/<company-name>-ipo-<id>.html
    This link-based approach is far more robust than table parsing because
    URL patterns change far less often than HTML layout.
    """
    import requests
    import re
    from bs4 import BeautifulSoup

    results = []
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept-Language': 'en-IN,en;q=0.9',
        'Referer': 'https://www.chittorgarh.com/',
    }

    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, 'html.parser')

    # ── Strategy 1: find IPO detail links (most reliable) ─────────────────
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        # Match pattern: /ipo/some-company-name-ipo-123.html
        if re.search(r'/ipo/[a-z0-9-]+-ipo-\d+\.html', href):
            name = a.get_text(strip=True)
            # Strip trailing "IPO" / "SME IPO" / "Rights Issue" suffixes
            name = re.sub(r'\s+(SME\s+)?IPO\s*$', '', name, flags=re.IGNORECASE).strip()
            name = re.sub(r'\s+Rights\s+Issue\s*$', '', name, flags=re.IGNORECASE).strip()
            name = ' '.join(name.split())   # collapse whitespace

            if name and 4 < len(name) < 120 and name.lower() not in seen:
                seen.add(name.lower())
                # Try to grab date info from sibling table cells
                parent = a.find_parent('td') or a.find_parent('li')
                row    = parent.find_parent('tr') if parent else None
                cols   = row.find_all('td') if row else []
                open_d  = cols[1].get_text(strip=True) if len(cols) > 1 else 'TBA'
                close_d = cols[2].get_text(strip=True) if len(cols) > 2 else 'TBA'
                price   = cols[3].get_text(strip=True) if len(cols) > 3 else 'TBA'

                results.append({
                    'name':       name,
                    'open_date':  open_d  or 'TBA',
                    'close_date': close_d or 'TBA',
                    'price_band': price   or 'TBA',
                    'source':     f'🗓 Chittorgarh ({label})',
                })

    # ── Strategy 2: fallback table parsing if no links found ──────────────
    if not results:
        print(f"[Chittorgarh] No links found for {url}, trying table fallback")
        JUNK = {'company', 'ipo name', 'name', 'issuer', ''}
        for table in soup.find_all('table'):
            for row in table.find_all('tr')[1:]:
                cols = row.find_all('td')
                if not cols:
                    continue
                a_tag = cols[0].find('a')
                name  = (a_tag.get_text(strip=True) if a_tag
                         else cols[0].get_text(separator=' ', strip=True))
                name  = ' '.join(name.split())
                if (name and 4 < len(name) < 120
                        and not name.startswith('₹')
                        and name.lower() not in JUNK
                        and name.lower() not in seen):
                    seen.add(name.lower())
                    open_d  = cols[1].get_text(strip=True) if len(cols) > 1 else 'TBA'
                    close_d = cols[2].get_text(strip=True) if len(cols) > 2 else 'TBA'
                    price   = cols[3].get_text(strip=True) if len(cols) > 3 else 'TBA'
                    results.append({
                        'name': name,
                        'open_date': open_d or 'TBA',
                        'close_date': close_d or 'TBA',
                        'price_band': price or 'TBA',
                        'source': f'🗓 Chittorgarh ({label})',
                    })

    print(f"[Chittorgarh] {label}: extracted {len(results)} IPOs from {url}")
    return results


def _fetch_nse_ipos():
    """
    Pull IPO data from NSE India's internal API.
    NSE requires a valid session cookie obtained by visiting the homepage first.
    """
    import requests

    results = []
    session = requests.Session()
    base_headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept-Language': 'en-US,en;q=0.9',
    }

    # Step 1: Get session cookies by visiting homepage
    session.get('https://www.nseindia.com', headers=base_headers, timeout=10)

    # Step 2: Hit the IPO API (endpoint for current allotment/subscription)
    api_headers = {**base_headers,
                   'Accept': 'application/json, text/plain, */*',
                   'Referer': 'https://www.nseindia.com/'}

    endpoints = [
        'https://www.nseindia.com/api/ipo-current-allotment',
        'https://www.nseindia.com/api/all-master-series-ipos',
    ]

    for ep in endpoints:
        try:
            r = session.get(ep, headers=api_headers, timeout=10)
            if r.status_code != 200:
                continue
            data = r.json()
            # data may be a list or a dict with a list inside
            items = data if isinstance(data, list) else data.get('data', [])
            for item in items:
                name = (item.get('companyName') or item.get('issuerName') or '').strip()
                if name and not _name_dup(name, results):
                    results.append({
                        'name':       name,
                        'open_date':  item.get('openDate')  or item.get('issueOpenDate')  or 'TBA',
                        'close_date': item.get('closeDate') or item.get('issueCloseDate') or 'TBA',
                        'price_band': str(item.get('issuePrice') or item.get('priceBand') or 'TBA'),
                        'source':     'NSE India',
                    })
            if results:
                break
        except Exception as e:
            print(f"[NSE] endpoint {ep} error: {e}")

    return results


def _fetch_nse_ipos_official():
    """
    Fetch IPO data directly from NSE official page.
    Source: https://www.nseindia.com/market-data/all-upcoming-issues-ipo

    This is a fallback scraper for current and upcoming IPOs from NSE.
    More reliable than third-party sources as it's the official exchange.
    """
    import requests
    from bs4 import BeautifulSoup

    results = []

    try:
        url = 'https://www.nseindia.com/market-data/all-upcoming-issues-ipo'
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }

        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, 'html.parser')

        # Find all tables (NSE pages use tables for data)
        for table in soup.find_all('table'):
            for row in table.find_all('tr')[1:]:  # Skip header
                cols = row.find_all('td')
                if len(cols) < 2:
                    continue

                # Extract company name (usually first column)
                name = cols[0].get_text(strip=True)
                if not name or len(name) < 3 or len(name) > 120:
                    continue

                # Extract dates if available
                open_date = cols[1].get_text(strip=True) if len(cols) > 1 else 'TBA'
                close_date = cols[2].get_text(strip=True) if len(cols) > 2 else 'TBA'
                price = cols[3].get_text(strip=True) if len(cols) > 3 else 'TBA'

                if name.lower() not in [r['name'].lower() for r in results]:
                    results.append({
                        'name':       name,
                        'open_date':  open_date or 'TBA',
                        'close_date': close_date or 'TBA',
                        'price_band': price or 'TBA',
                        'source':     '🏛️ NSE Official',
                    })

        print(f"[NSE Official] Fetched {len(results)} IPOs from NSE official page")

    except Exception as e:
        print(f"[NSE Official] Error: {e}")

    return results


def _fetch_nse_bse_api():
    """
    Fetch IPO data using nse-bse-api (open-source npm package).

    This calls the nse-bse-api Node.js package to get IPO information.
    The package provides: current, past, and upcoming IPOs.

    Installation: npm install nse-bse-api (in project root)
    """
    import subprocess
    import json

    results = []

    try:
        # Check if nse-bse-api is installed
        # Create a simple Node.js script to fetch IPO data
        node_script = """
const nseApi = require('nse-bse-api');
const { getAllIPOs } = nseApi;

async function fetchIPOs() {
    try {
        const ipos = await getAllIPOs();
        console.log(JSON.stringify(ipos));
    } catch (error) {
        console.error('Error fetching IPOs:', error.message);
    }
}

fetchIPOs();
"""

        # Try to run the script
        proc = subprocess.run(
            ['node', '-e', node_script],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=os.path.dirname(os.path.dirname(__file__))  # Project root
        )

        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout)
            if isinstance(data, list):
                for ipo in data:
                    name = ipo.get('name', '').strip()
                    if name and 3 < len(name) < 120:
                        results.append({
                            'name':       name,
                            'open_date':  ipo.get('openDate', 'TBA') or 'TBA',
                            'close_date': ipo.get('closeDate', 'TBA') or 'TBA',
                            'price_band': str(ipo.get('priceband', 'TBA')),
                            'source':     '📦 NSE-BSE API',
                        })
            print(f"[NSE-BSE API] Fetched {len(results)} IPOs")
        else:
            print(f"[NSE-BSE API] Not installed or error. Install with: npm install nse-bse-api")

    except subprocess.TimeoutExpired:
        print("[NSE-BSE API] Timeout (Node.js may not be installed)")
    except FileNotFoundError:
        print("[NSE-BSE API] Node.js not found in PATH")
    except Exception as e:
        print(f"[NSE-BSE API] Error: {e}")

    return results


def _fetch_ipoalerts():
    """
    Fetch IPO data from ipoalerts.in API — comprehensive aggregator for NSE/BSE IPOs.

    ipoalerts aggregates data from multiple sources including NSE, BSE, and other
    financial data providers. Returns both open and upcoming IPOs.

    Note: ipoalerts.in has free tier access — you can sign up at https://ipoalerts.in/signup
    and add your API key to settings.json as 'ipoalerts_api_key' for GMP data.
    Without API key, we get partial results (capped at 1 IPO) but cache refreshes every 30min.
    """
    import requests

    results = []

    # Try to read API key from config (optional — free tier works without it)
    api_key = None
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r') as f:
                config = json.load(f)
                # Read from external_apis.ipoalerts.api_key
                api_key = config.get('external_apis', {}).get('ipoalerts', {}).get('api_key')
    except Exception as e:
        print(f"[IPOAlerts] Config read error: {e}")

    headers = {}
    if api_key:
        headers['x-api-key'] = api_key
        print(f"[IPOAlerts] Using API key for full access")
    else:
        print(f"[IPOAlerts] No API key configured — using free tier (limited results)")

    headers['User-Agent'] = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    )

    # Fetch IPOs — free plan only supports 'open' status
    # (upcoming, announced, listed, closed are paid-only)
    statuses = ['open']

    for status in statuses:
        try:
            # Try basic request first
            url = f'https://api.ipoalerts.in/ipos?status={status}'
            print(f"[IPOAlerts] Requesting: {url}")
            resp = requests.get(url, headers=headers, timeout=15)

            print(f"[IPOAlerts] status={status} → HTTP {resp.status_code}")

            if resp.status_code not in (200, 201):
                try:
                    error_data = resp.json()
                    print(f"[IPOAlerts] Error response: {error_data}")
                except:
                    print(f"[IPOAlerts] Response text: {resp.text[:300]}")
                continue

            data = resp.json()
            ipos = data.get('ipos', [])
            print(f"[IPOAlerts] status={status}: {len(ipos)} IPOs fetched")

            for ipo in ipos:
                name = ipo.get('name', '').strip()
                if not name or len(name) < 3 or len(name) > 120:
                    continue

                # Parse dates — ipoalerts returns ISO format
                open_date  = ipo.get('openDate', 'TBA')
                close_date = ipo.get('closeDate', 'TBA')

                # Extract just the date part if it's a full datetime
                if open_date and 'T' in str(open_date):
                    open_date = open_date.split('T')[0]
                if close_date and 'T' in str(close_date):
                    close_date = close_date.split('T')[0]

                results.append({
                    'name':       name,
                    'open_date':  open_date or 'TBA',
                    'close_date': close_date or 'TBA',
                    'price_band': ipo.get('priceband', 'TBA') or 'TBA',
                    'source':     '📊 IPOAlerts (API)',
                })

        except Exception as e:
            print(f"[IPOAlerts] status={status} error: {e}")

    print(f"[IPOAlerts] Total: {len(results)} IPOs fetched")
    return results


def _name_dup(name, lst):
    """Case-insensitive duplicate check."""
    nl = name.lower()
    return any(r.get('name', '').lower() == nl for r in lst)


# ─────────────────────────────────────────────────────────────────────────────
# Per-query live search: Yahoo Finance
# ─────────────────────────────────────────────────────────────────────────────

def _search_yahoo(query, timeout=8):
    """
    Live search via Yahoo Finance — no API key needed.
    To get Indian stocks (NSE/BSE), we search with .NS/.BO suffix to force India results.
    Yahoo Finance returns global results unless we explicitly ask for Indian stocks.
    """
    import requests

    results = []

    # Try multiple query variations to find Indian stocks
    # Start with .NS (NSE) then .BO (BSE), then plain
    query_variations = [
        f"{query}.NS",  # Try NSE first
        f"{query}.BO",  # Try BSE second
        query,          # Fall back to plain query
    ]

    for query_variant in query_variations:
        if results:
            break  # Got results, stop trying variations

        # Try query1 first, fall back to query2 if it fails
        for host in ('query1', 'query2'):
            url = (
                f'https://{host}.finance.yahoo.com/v1/finance/search'
                f'?q={requests.utils.quote(query_variant)}'
                '&lang=en-US&region=IN'
                '&quotesCount=15&newsCount=0'
                '&enableFuzzyQuery=true'
            )
            headers = {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0.0.0 Safari/537.36'
                ),
                'Accept': 'application/json',
            }

            try:
                resp = requests.get(url, headers=headers, timeout=timeout)
                if resp.status_code != 200:
                    print(f"[Yahoo/{host}] query='{query_variant}' HTTP {resp.status_code}")
                    continue

                data = resp.json()
                all_quotes = data.get('quotes', [])
                print(f"[Yahoo/{host}] query='{query_variant}' → {len(all_quotes)} quotes")

                # DEBUG: Log quotes when searching with suffix
                if len(all_quotes) > 0 and ('.' in query_variant):
                    print(f"[Yahoo/{host}] DEBUG: first 3 quotes from '{query_variant}':")
                    for i, q in enumerate(all_quotes[:3]):
                        print(f"  [{i}] {q.get('symbol')} ({q.get('exchange')}) - {q.get('longname')}")

                # Process quotes
                for quote in all_quotes:
                    symbol = quote.get('symbol', '')
                    name = (quote.get('longname') or quote.get('shortname') or '').strip()
                    quote_type = quote.get('quoteType', '')
                    exchange = quote.get('exchange', '')

                    # ── Filter: Accept if it's an Indian equity (NSE/BSE) ──
                    is_indian = (symbol.endswith('.NS') or symbol.endswith('.BO') or
                                exchange in ('NSE', 'BSE', 'India', 'NSE India', 'BSE India'))

                    if not is_indian:
                        continue
                    if quote_type not in ('EQUITY', 'FUTURE', ''):
                        continue
                    if not name or len(name) < 3 or len(name) > 120:
                        continue

                    exchange_label = 'NSE' if symbol.endswith('.NS') else ('BSE' if symbol.endswith('.BO') else exchange or 'NSE')
                    results.append({
                        'name':       name,
                        'symbol':     symbol,
                        'open_date':  'Listed',
                        'close_date': 'Listed',
                        'price_band': exchange_label,
                        'source':     '🌐 Yahoo Finance (Live)',
                    })

                if results:
                    break  # Got results from this query_variant, stop trying hosts

            except Exception as e:
                print(f"[Yahoo/{host}] error: {e}")

    print(f"[Yahoo] returning {len(results)} results for '{query}'")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard & Home Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/', methods=['GET'])
def index():
    """Serve dashboard homepage"""
    from flask import Response

    dashboard_path = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'ipo_dashboard_v2.html')
    print(f"[ROUTE /] Attempting to serve: {dashboard_path}")
    print(f"[ROUTE /] File exists: {os.path.exists(dashboard_path)}")

    if os.path.exists(dashboard_path):
        try:
            with open(dashboard_path, 'r', encoding='utf-8') as f:
                content = f.read()
                print(f"[ROUTE /] Successfully read {len(content)} bytes")
                # Use Response object for proper header handling
                resp = Response(content, 200, mimetype='text/html; charset=utf-8')
                resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                resp.headers['Pragma'] = 'no-cache'
                resp.headers['Expires'] = '0'
                return resp
        except Exception as e:
            print(f"[ROUTE /] Error reading file: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'status': 'error', 'message': f'Error reading file: {e}'}), 500
    else:
        print(f"[ROUTE /] File NOT found at: {dashboard_path}")
        return jsonify({'status': 'error', 'message': f'Dashboard not found at {dashboard_path}'}), 404


@app.route('/dashboard', methods=['GET'])
def dashboard():
    """Serve dashboard (alias for /)"""
    return index()


@app.route('/test', methods=['GET'])
def test_route():
    """Simple test route - returns plain HTML"""
    print("[ROUTE /test] Test route hit!")
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>Flask Test</title></head>
    <body style="font-family: Arial; padding: 40px; background: #1a1a1a; color: #fff;">
        <h1>✅ FLASK IS WORKING!</h1>
        <p>If you see this, Flask route is responding.</p>
        <hr>
        <h2>Testing Dashboard:</h2>
        <button onclick="testDashboard()">Load Real Dashboard</button>
        <div id="status"></div>
        <script>
            function testDashboard() {
                document.getElementById('status').innerHTML = 'Loading...';
                fetch('/api/dashboard')
                    .then(r => r.json())
                    .then(d => document.getElementById('status').innerHTML = '<pre>' + JSON.stringify(d, null, 2) + '</pre>')
                    .catch(e => document.getElementById('status').innerHTML = 'Error: ' + e);
            }
        </script>
    </body>
    </html>
    """
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}


# ─────────────────────────────────────────────────────────────────────────────
# Search endpoint
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/search-ipos', methods=['GET'])
def api_search_ipos():
    """
    Real-time IPO search — pure internet, no database, no static lists.

    Every call does TWO things in parallel:
      A) Yahoo Finance live search for the typed name (~2s)
      B) Filter the background-loaded Chittorgarh/NSE cache (instant)

    Both complete before the response is sent (max 10s total).
    Frontend timeout is 15s, so we always have headroom.
    """
    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return jsonify({'status': 'success', 'query': query,
                        'count': 0, 'results': [], 'sources': []})

    q_lower = query.lower()
    results = []
    sources_hit = []

    # ── A) Yahoo Finance + B) Cache filter — run in PARALLEL ────────────────
    # Yahoo Finance: per-query live search (~2-4s)
    # Cache: instant filter of Chittorgarh/NSE data loaded in background

    yahoo_results  = []
    yahoo_error    = [None]

    def _run_yahoo():
        try:
            yahoo_results.extend(_search_yahoo(query, timeout=10))
        except Exception as e:
            yahoo_error[0] = str(e)

    yahoo_thread = threading.Thread(target=_run_yahoo, daemon=True)
    yahoo_thread.start()

    # Kick off cache refresh if stale — completely non-blocking
    # NEVER wait for cache in the search path
    if _cache_stale():
        threading.Thread(target=_load_cache, daemon=True).start()

    with _cache_lock:
        cached = list(_ipo_cache['data'])

    # Wait for Yahoo Finance (up to 11s — frontend timeout is 15s)
    yahoo_thread.join(timeout=11)

    if yahoo_results:
        results.extend(yahoo_results)
        sources_hit.append('yahoo_finance')
    if yahoo_error[0]:
        print(f"[Yahoo] thread error: {yahoo_error[0]}")

    for ipo in cached:
        if q_lower in ipo['name'].lower():
            if not _name_dup(ipo['name'], results):
                src = ipo.get('source', 'Internet')
                results.append({
                    'name':       ipo['name'],
                    'open_date':  ipo.get('open_date',  'TBA'),
                    'close_date': ipo.get('close_date', 'TBA'),
                    'price_band': ipo.get('price_band', 'TBA'),
                    'source':     src,
                })
                skey = src.split('(')[0].strip().lower().replace(' ', '_')
                if skey not in sources_hit:
                    sources_hit.append(skey)

    print(f"[SEARCH] '{query}' → {len(results)} results | sources: {sources_hit}")

    return jsonify({
        'status':           'success',
        'query':            query,
        'count':            len(results[:15]),
        'results':          results[:15],
        'sources':          sources_hit,
        'cache_size':       len(cached),
        'cache_age_min':    round((_cache_age_seconds() or 0) / 60, 1),
        'timestamp':        datetime.now().isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Add IPO to tracking (saves to DB so it shows in the dashboard)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/add-ipo', methods=['POST'])
def api_add_ipo():
    """Add IPO to tracking with all scraped details"""
    try:
        data     = request.get_json()
        ipo_name = data.get('ipo_name', '').strip()
        sector   = data.get('sector', 'TBA')

        print(f"[API Add] Adding IPO: {ipo_name}")
        print(f"[API Add] Data received: {json.dumps(data, indent=2)}")

        if not ipo_name:
            print("[API Add] Error: ipo_name is empty")
            return jsonify({'status': 'error', 'message': 'ipo_name required'}), 400

        print(f"[API Add] Database path: {DB_PATH}")
        conn   = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if already tracking
        cursor.execute("SELECT ipo_id FROM IPOs WHERE LOWER(ipo_name) = LOWER(?)", (ipo_name,))
        existing = cursor.fetchone()
        if existing:
            print(f"[API Add] IPO already exists: {ipo_name}")
            conn.close()
            return jsonify({'status': 'success',
                            'message': f'✅ "{ipo_name}" is already being tracked'})

        # Extract all optional fields from scraped details
        issue_price = str(data.get('issue_price', '')).strip()
        issue_size_cr = str(data.get('issue_size_cr', '')).strip()
        open_date = str(data.get('open_date', '')).strip()
        close_date = str(data.get('close_date', '')).strip()
        listing_date = str(data.get('listing_date', '')).strip()
        listing_price = str(data.get('listing_price', '')).strip()
        gmp_data = str(data.get('gmp', '')).strip()  # Should be like "+15%" or "₹123"
        qib_sub = str(data.get('qib_subscription', '')).strip()
        hni_sub = str(data.get('hni_subscription', '')).strip()
        retail_sub = str(data.get('retail_subscription', '')).strip()
        status = str(data.get('status', 'TBA')).strip()

        # Clean up empty strings
        issue_price = issue_price if issue_price else ''
        issue_size_cr = issue_size_cr if issue_size_cr else ''
        open_date = open_date if open_date else ''
        close_date = close_date if close_date else ''
        listing_date = listing_date if listing_date else ''
        listing_price = listing_price if listing_price else ''
        gmp_data = gmp_data if gmp_data else ''
        qib_sub = qib_sub if qib_sub else ''
        hni_sub = hni_sub if hni_sub else ''
        retail_sub = retail_sub if retail_sub else ''
        status = status if status else 'TBA'

        print(f"[API Add] Inserting with status: {status}")

        # Insert IPO with all details
        cursor.execute("""
            INSERT INTO IPOs (ipo_name, sector, issue_price, issue_size_cr,
                              open_date, close_date, listing_date, listing_price,
                              gmp_data, qib_subscription, hni_subscription, retail_subscription,
                              status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ipo_name, sector, issue_price, issue_size_cr,
              open_date, close_date, listing_date, listing_price,
              gmp_data, qib_sub, hni_sub, retail_sub, status,
              datetime.now().isoformat(),
              datetime.now().isoformat()))

        conn.commit()
        inserted_id = cursor.lastrowid
        print(f"[API Add] ✅ Successfully inserted IPO with ID: {inserted_id}")

        # Verify insertion
        cursor.execute("SELECT ipo_name FROM IPOs WHERE ipo_id = ?", (inserted_id,))
        verify = cursor.fetchone()
        if verify:
            print(f"[API Add] ✅ Verified: {verify['ipo_name']} inserted successfully")
        else:
            print(f"[API Add] ❌ WARNING: Could not verify insertion")

        conn.close()

        return jsonify({
            'status': 'success',
            'message': f'✅ "{ipo_name}" added to your tracking list!',
            'ipo_id': inserted_id
        })
    except Exception as e:
        print(f"[API Add] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/delete-ipo', methods=['POST', 'DELETE'])
def api_delete_ipo():
    """Delete an IPO from tracking list"""
    try:
        # Support both POST (with JSON body) and DELETE (with JSON body)
        data = request.get_json()
        ipo_id = data.get('ipo_id')
        ipo_name = data.get('ipo_name')

        if not ipo_id and not ipo_name:
            return jsonify({'status': 'error', 'message': 'ipo_id or ipo_name required'}), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        if ipo_id:
            cursor.execute("DELETE FROM IPOs WHERE ipo_id = ?", (ipo_id,))
        else:
            cursor.execute("DELETE FROM IPOs WHERE LOWER(ipo_name) = LOWER(?)", (ipo_name,))

        conn.commit()
        rows_deleted = cursor.rowcount
        conn.close()

        if rows_deleted == 0:
            return jsonify({'status': 'error', 'message': 'IPO not found'}), 404

        name = ipo_name or f"IPO #{ipo_id}"
        return jsonify({
            'status': 'success',
            'message': f'✅ "{name}" removed from tracking list',
            'rows_deleted': rows_deleted
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


# ─────────────────────────────────────────────────────────────────────────────
# Chittorgarh Scraping Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/search-chittorgarh', methods=['GET'])
def api_search_chittorgarh():
    """Search for IPOs - Primary: IPO Ji (complete data), Fallback: Chittorgarh"""
    query = request.args.get('q', '').strip()

    if len(query) < 2:
        return jsonify({
            'status': 'error',
            'message': 'Query must be at least 2 characters',
            'results': []
        }), 400

    try:
        results = []
        source = None

        # PRIMARY: Try IPO Ji (has complete data: QIB, HNI, Retail, GMP, dates)
        print(f"[API Search] Searching IPO Ji for: {query}")
        try:
            results = ipoji_scraper.search_ipos(query)
            if results:
                source = 'ipoji'
                print(f"[API Search] ✅ Found {len(results)} IPO(s) on IPO Ji")
        except Exception as e:
            print(f"[API Search] IPO Ji error: {e}")

        # FALLBACK: If not found, try Chittorgarh
        if not results:
            print(f"[API Search] IPO Ji didn't find it, trying Chittorgarh fallback...")
            try:
                results = chittorgarh_scraper.search_ipos(query)
                if results:
                    source = 'chittorgarh'
                    print(f"[API Search] ✅ Found {len(results)} IPO(s) on Chittorgarh")
            except Exception as e:
                print(f"[API Search] Chittorgarh error: {e}")

        # Remove duplicates while preserving order
        seen = set()
        unique_results = []
        for result in results:
            # Skip invalid results
            if not result.get('name') or not result.get('url'):
                continue

            # Create a unique key from name and URL
            key = (result.get('name', '').lower().strip(), result.get('url', '').lower().strip())
            if key not in seen:
                seen.add(key)
                # Ensure all required fields exist
                result.setdefault('source', 'unknown')
                result.setdefault('status', 'TBA')
                result.setdefault('open_date', '')
                result.setdefault('close_date', '')
                unique_results.append(result)

        print(f"[API Search] Deduplicated: {len(results)} results → {len(unique_results)} unique")
        if len(unique_results) != len(results):
            print(f"[API Search] ℹ️  Removed {len(results) - len(unique_results)} duplicate/invalid results")

        return jsonify({
            'status': 'success',
            'query': query,
            'count': len(unique_results),
            'results': unique_results,
            'source': source or 'unknown',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        print(f"[API Search] Error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
            'results': []
        }), 500


@app.route('/api/scrape-ipo-details', methods=['POST'])
def api_scrape_ipo_details():
    """Scrape detailed IPO info - Primary: IPO Ji, Fallback: Chittorgarh (NO database save)"""
    try:
        data = request.get_json()
        ipo_url = data.get('url')
        ipo_name = data.get('name', 'Unknown')
        source = data.get('source', 'unknown')  # Track source preference

        if not ipo_url:
            return jsonify({'status': 'error', 'message': 'url required'}), 400

        details = None
        scrape_source = None

        # PRIMARY: Try IPO Ji first (better data completeness)
        if 'ipoji.com' in ipo_url or source == 'ipoji':
            print(f"[API Scrape] Trying IPO Ji for: {ipo_name}")
            try:
                details = ipoji_scraper.scrape_ipo_details(ipo_url)
                if not details.get('error'):
                    scrape_source = 'ipoji'
                    print(f"[API Scrape] ✅ IPO Ji scrape successful")
            except Exception as e:
                print(f"[API Scrape] IPO Ji error: {e}")

        # FALLBACK: If IPO Ji failed or wasn't the source, try Chittorgarh
        if not details or details.get('error'):
            print(f"[API Scrape] Trying Chittorgarh fallback for: {ipo_name}")
            try:
                details = chittorgarh_scraper.scrape_ipo_details(ipo_url)
                if not details.get('error'):
                    scrape_source = 'chittorgarh'
                    print(f"[API Scrape] ✅ Chittorgarh scrape successful")
            except Exception as e:
                print(f"[API Scrape] Chittorgarh error: {e}")

        # Check if scraping succeeded
        if not details or details.get('error'):
            return jsonify({
                'status': 'error',
                'message': f'Failed to scrape from any source'
            }), 500

        # Enrich with GMP from ipowatch.in if not already present
        if not details.get('gmp') or details.get('gmp') in ['', '—', 'TBA']:
            print(f"[API Scrape] Fetching GMP from ipowatch.in for: {ipo_name}")
            try:
                gmp_info = lookup_gmp(details.get('name', ipo_name))
                if gmp_info:
                    details['gmp'] = gmp_info['gmp']
                    details['gmp_value'] = gmp_info['gmp_value']
                    details['gmp_trend'] = gmp_info.get('trend', '')
                    details['est_listing'] = gmp_info.get('est_listing', '')
                    print(f"[API Scrape] ✅ GMP enriched: {gmp_info['gmp']}")
                else:
                    print(f"[API Scrape] No GMP data found for: {ipo_name}")
            except Exception as e:
                print(f"[API Scrape] GMP fetch error: {e}")

        # Calculate minimum investment if we have price and lot size
        if details.get('issue_price') and details.get('lot_size'):
            try:
                price = float(str(details['issue_price']).replace(',', ''))
                lot = int(str(details['lot_size']).replace(',', ''))
                details['min_investment'] = f"₹{price * lot:,.0f}"
                print(f"[API Scrape] Min investment: {details['min_investment']}")
            except:
                pass

        # Return the scraped details - don't save to database
        # The add-ipo endpoint will handle database persistence
        return jsonify({
            'status': 'success',
            'message': f'✅ Scraped details for "{details.get("name", ipo_name)}"',
            'ipo_name': details.get('name') or ipo_name,
            'details': details,
            'source': scrape_source
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


def _parse_number(val):
    """Parse numeric strings, return 0 if invalid"""
    if not val or val == 'TBA':
        return 0
    try:
        return float(str(val).replace(',', '').replace('₹', '').strip())
    except:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Server-side Logic Score Calculator (mirrors frontend v1.1)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_server_score(ipo_row):
    """Calculate Logic Score server-side. Mirrors frontend calculateLogicScore exactly."""
    score = 50  # Base score

    def parse_val(s):
        if not s or s in ('—', 'TBA'):
            return 0
        import re
        m = re.search(r'[\d.]+', str(s))
        return float(m.group(0)) if m else 0

    qib = parse_val(ipo_row.get('qib_subscription'))
    hni = parse_val(ipo_row.get('hni_subscription'))
    retail = parse_val(ipo_row.get('retail_subscription'))

    # Criterion 1: QIB (30%)
    if qib > 0:
        if qib > 10: score += 15
        elif qib > 5: score += 10
        elif qib > 2: score += 5
        else: score -= 5
        if qib > 5: score += 3  # QIB bonus

    # Criterion 2: Total Subscription (20%)
    subs = [x for x in [qib, hni, retail] if x > 0]
    if subs:
        avg = sum(subs) / len(subs)
        if avg > 10: score += 10
        elif avg > 5: score += 8
        elif avg > 2: score += 4
        elif avg > 0: score -= 3

    # Criterion 3: GMP (15%)
    gmp_str = str(ipo_row.get('gmp_data') or ipo_row.get('gmp') or '')
    import re
    gmp_m = re.search(r'([+-]?\d+\.?\d*)', gmp_str)
    gmp_val = float(gmp_m.group(1)) if gmp_m else 0
    if gmp_val != 0:
        if gmp_val > 30: score += 12
        elif gmp_val > 15: score += 10
        elif gmp_val > 5: score += 6
        elif gmp_val > 0: score += 2
        elif gmp_val < -15: score -= 8
        elif gmp_val < -5: score -= 3

    # Criterion 4: Sector (15%)
    sector = (ipo_row.get('sector') or '').lower()
    strong = ['technology', 'fintech', 'it', 'pharma', 'healthcare']
    moderate = ['chemicals', 'manufacturing', 'industrial']
    if any(s in sector for s in strong): score += 8
    elif any(s in sector for s in moderate): score += 3
    elif 'sme' in sector: score += 2

    # Criterion 5: Issue Size (7%)
    issue_str = str(ipo_row.get('issue_size_cr') or '')
    issue_m = re.search(r'[\d.]+', issue_str)
    if issue_m:
        issue_cr = float(issue_m.group(0))
        if issue_cr > 500: score += 5
        elif issue_cr > 100: score += 3
        elif issue_cr > 20: score += 1
        else: score -= 2

    return max(0, min(100, round(score)))


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard endpoints
# ─────────────────────────────────────────────────────────────────────────────

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_db_schema():
    """Ensure database has all required columns"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Check if IPOs table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='IPOs'")
        if not cur.fetchone():
            print("[DB] Creating IPOs table...")
            cur.execute("""
                CREATE TABLE IPOs (
                    ipo_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ipo_name TEXT NOT NULL,
                    sector TEXT,
                    issue_price TEXT,
                    issue_size_cr TEXT,
                    open_date TEXT,
                    close_date TEXT,
                    listing_date TEXT,
                    listing_price TEXT,
                    gmp_data TEXT,
                    qib_subscription TEXT,
                    hni_subscription TEXT,
                    retail_subscription TEXT,
                    status TEXT,
                    announcement_date TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.commit()
        else:
            # Add missing columns if they don't exist
            cur.execute("PRAGMA table_info(IPOs)")
            columns = {row[1] for row in cur.fetchall()}

            missing = [
                ('qib_subscription', 'TEXT'),
                ('hni_subscription', 'TEXT'),
                ('retail_subscription', 'TEXT'),
                ('gmp_data', 'TEXT'),
                ('listing_price', 'TEXT'),
                ('open_date', 'TEXT'),
                ('close_date', 'TEXT'),
                ('status', 'TEXT'),
                ('previous_score', 'INTEGER DEFAULT 50'),
            ]

            for col_name, col_type in missing:
                if col_name not in columns:
                    try:
                        cur.execute(f"ALTER TABLE IPOs ADD COLUMN {col_name} {col_type}")
                        print(f"[DB] Added column: {col_name}")
                    except:
                        pass

            conn.commit()

        conn.close()
    except Exception as e:
        print(f"[DB] Schema error: {e}")


def get_active_ipos():
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
            SELECT ipo_id, ipo_name, sector, issue_price, issue_size_cr,
                   open_date, close_date, listing_date, listing_price,
                   gmp_data, qib_subscription, hni_subscription, retail_subscription,
                   status, updated_at
            FROM IPOs ORDER BY ipo_id DESC LIMIT 20
        """)
        rows = cur.fetchall()
        conn.close()

        result = []
        for r in rows:
            result.append({
                'id': r['ipo_id'],
                'name': r['ipo_name'],
                'sector': r['sector'] or 'TBA',
                'issue_price': r['issue_price'] or 'TBA',
                'issue_size_cr': r['issue_size_cr'] or 'TBA',
                'open_date': r['open_date'] or 'TBA',
                'close_date': r['close_date'] or 'TBA',
                'listing_date': r['listing_date'] or 'TBA',
                'listing_price': r['listing_price'] or 'TBA',
                'gmp': r['gmp_data'] or '—',
                'qib_subscription': r['qib_subscription'] or '—',
                'hni_subscription': r['hni_subscription'] or '—',
                'retail_subscription': r['retail_subscription'] or '—',
                'status': r['status'] or 'TBA',
                'updated_at': r['updated_at'] or datetime.now().isoformat()
            })
        return result
    except Exception as e:
        print(f"[Dashboard] active IPOs error: {e}");
        return []


def get_listing_tracker():
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("""
            SELECT i.ipo_name,i.sector,i.listing_price,l.price,l.gain_percent,l.timestamp
            FROM Listing_Data l JOIN IPOs i ON l.ipo_id=i.ipo_id
            ORDER BY l.timestamp DESC LIMIT 10
        """)
        rows = cur.fetchall(); conn.close()
        return [{'name': r['ipo_name'], 'sector': r['sector'],
                 'listing_price': round(r['listing_price'] or 100, 2),
                 'current_price': round(r['price'] or 100, 2),
                 'gain_percent':  round(r['gain_percent'] or 0, 2),
                 'listing_date':  str(r['timestamp'])} for r in rows]
    except Exception as e:
        print(f"[Dashboard] listing tracker error: {e}"); return []


def get_recent_alerts():
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT alert_type,alert_title,created_at,ipo_id FROM Alerts ORDER BY created_at DESC LIMIT 10")
        rows = cur.fetchall(); conn.close()
        return [{'type': r['alert_type'], 'ipo_name': f'IPO #{r["ipo_id"]}',
                 'subject': r['alert_title'], 'timestamp': r['created_at']} for r in rows]
    except Exception as e:
        print(f"[Dashboard] alerts error: {e}"); return []


def get_countdown():
    from datetime import timezone as tz
    ist = tz(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    t   = now.replace(hour=14, minute=0, second=0, microsecond=0)
    if now > t: t += timedelta(days=1)
    d   = t - now
    h, m, s = d.seconds//3600, (d.seconds%3600)//60, d.seconds%60
    return {'countdown': f"{h:02d}:{m:02d}:{s:02d}",
            'alert_time': t.strftime('%Y-%m-%d %H:%M IST'),
            'is_today': t.date() == now.date()}


@app.route('/api/refresh-gmp', methods=['POST'])
def api_refresh_gmp():
    """Refresh GMP data for all tracked IPOs + check score changes for email alerts"""
    try:
        gmp_data = fetch_all_gmp()
        if not gmp_data:
            return jsonify({'status': 'error', 'message': 'Could not fetch GMP data'}), 500

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""SELECT ipo_id, ipo_name, sector, issue_price, issue_size_cr,
                          gmp_data, qib_subscription, hni_subscription, retail_subscription,
                          open_date, close_date, status, previous_score
                          FROM IPOs""")
        ipos = cursor.fetchall()

        updated = 0
        alerts_sent = 0
        for ipo in ipos:
            ipo_dict = dict(ipo)
            gmp_info = lookup_gmp(ipo['ipo_name'], gmp_data)
            if gmp_info and gmp_info['gmp']:
                ipo_dict['gmp_data'] = gmp_info['gmp']
                cursor.execute(
                    "UPDATE IPOs SET gmp_data = ?, updated_at = ? WHERE ipo_id = ?",
                    (gmp_info['gmp'], datetime.now().isoformat(), ipo['ipo_id'])
                )
                updated += 1
                print(f"[GMP Refresh] {ipo['ipo_name']}: {gmp_info['gmp']}")

            # Calculate new score and check for decision change
            new_score = calculate_server_score(ipo_dict)
            old_score = int(ipo['previous_score'] or 50)

            if new_score != old_score:
                print(f"[Score] {ipo['ipo_name']}: {old_score} -> {new_score}")
                # Check if decision upgraded and send email
                if check_and_alert(ipo['ipo_name'], old_score, new_score, ipo_dict):
                    alerts_sent += 1

                # Save new score as previous_score
                cursor.execute(
                    "UPDATE IPOs SET previous_score = ? WHERE ipo_id = ?",
                    (new_score, ipo['ipo_id'])
                )

        conn.commit()
        conn.close()

        result = {'status': 'success', 'gmp_updated': updated, 'alerts_sent': alerts_sent,
                  'email_configured': email_configured()}
        print(f"[Refresh] GMP updated: {updated}, Alerts sent: {alerts_sent}")
        return jsonify(result)
    except Exception as e:
        print(f"[GMP Refresh] Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/dashboard', methods=['GET'])
def api_dashboard():
    return jsonify({
        'status': 'success',
        'active_ipos':     {'count': len(get_active_ipos()),    'data': get_active_ipos()},
        'listing_tracker': {'count': len(get_listing_tracker()), 'data': get_listing_tracker()},
        'alerts':          {'count': len(get_recent_alerts()),   'data': get_recent_alerts()},
        'critical_alert':  get_countdown(),
        'timestamp':       datetime.now().isoformat()
    })


@app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({
        'status':       'ok',
        'message':      'IPO Analyzer API running',
        'search_mode':  'internet_only',
        'cache_size':   len(_ipo_cache['data']),
        'cache_age_min': round((_cache_age_seconds() or 0) / 60, 1),
        'cache_stale':  _cache_stale(),
        'timestamp':    datetime.now().isoformat()
    })


@app.route('/api/cache-status', methods=['GET'])
@app.route('/api/cache-status', methods=['GET'])
def api_cache_status():
    with _cache_lock:
        return jsonify({
            'status':       'success',
            'size':         len(_ipo_cache['data']),
            'loaded_at':    _ipo_cache['loaded_at'].isoformat() if _ipo_cache['loaded_at'] else None,
            'age_minutes':  round((_cache_age_seconds() or 0) / 60, 1),
            'stale':        _cache_stale(),
            'sample':       _ipo_cache['data'][:5],
        })


def _update_tracked_ipos():
    """Background job: Refresh details for all tracked IPOs. Runs every 2 hours."""
    while True:
        time.sleep(7200)
        print("\n" + "=" * 60)
        print("[SCHEDULER] Starting IPO details refresh")
        print("=" * 60)
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT ipo_id, ipo_name, gmp_data FROM IPOs ORDER BY updated_at ASC LIMIT 10")
            ipos = cursor.fetchall()
            conn.close()
            for ipo in ipos:
                try:
                    gmp_data_str = ipo['gmp_data'] or '{}'
                    try:
                        gmp_obj = json.loads(gmp_data_str)
                        ipo_url = gmp_obj.get('url', '')
                    except (json.JSONDecodeError, TypeError):
                        ipo_url = ''
                    if not ipo_url:
                        ipo_slug = ipo['ipo_name'].lower().replace(' ', '-')
                        ipo_url = f"https://www.chittorgarh.com/ipo/{ipo_slug}-ipo/"
                    print(f"[SCHEDULER] Updating: {ipo['ipo_name']}")
                    details = chittorgarh_scraper.scrape_ipo_details(ipo_url)
                    if not details.get('error'):
                        conn2 = sqlite3.connect(DB_PATH)
                        c2 = conn2.cursor()
                        c2.execute("UPDATE IPOs SET gmp_data=?, updated_at=? WHERE ipo_id=?",
                                   (json.dumps(details), datetime.now().isoformat(), ipo['ipo_id']))
                        conn2.commit()
                        conn2.close()
                    time.sleep(1)
                except Exception as e:
                    print(f"  Error updating {ipo['ipo_name']}: {e}")
        except Exception as e:
            print(f"[SCHEDULER] Error: {e}")
        print("[SCHEDULER] Details refresh complete\n")


def _prewarm():
    time.sleep(1)
    _load_cache()
    threading.Thread(target=_update_tracked_ipos, daemon=True).start()


if __name__ == '__main__':
    print("=" * 60)
    print("  IPO Analyzer Flask API")
    print("  http://localhost:5000")
    print("=" * 60)
    print()
    _ensure_db_schema()
    print()
    print("  Endpoints:")
    print("    GET  /api/search-chittorgarh?q=<name>")
    print("    POST /api/scrape-ipo-details")
    print("    POST /api/add-ipo")
    print("    POST /api/refresh-gmp  (GMP + score alerts)")
    print("    GET  /api/dashboard")
    print()
    print(f"  Email alerts: {'CONFIGURED' if email_configured() else 'NOT CONFIGURED (edit config/settings.json)'}")
    print()
    threading.Thread(target=_prewarm, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
