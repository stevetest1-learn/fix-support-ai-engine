"""
FIX Support AI Engine — Coinbase Market Data Lambda

Fetches real-time bid/ask market data from the Coinbase Exchange
public REST API. No API key required for ticker and stats endpoints.

Endpoints used (public, no authentication):
  GET https://api.exchange.coinbase.com/products/{product_id}/ticker
  GET https://api.exchange.coinbase.com/products/{product_id}/stats

Called via:  GET /market-data?product=BTC-USD
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────────────────────
# SUPPORTED PRODUCTS
# ─────────────────────────────────────────────────────────────────────────────
SUPPORTED_PRODUCTS = {
    'BTC-USD':  {'name': 'Bitcoin',       'base': 'BTC',  'icon': '₿'},
    'ETH-USD':  {'name': 'Ethereum',      'base': 'ETH',  'icon': 'Ξ'},
    'LTC-USD':  {'name': 'Litecoin',      'base': 'LTC',  'icon': 'Ł'},
    'SOL-USD':  {'name': 'Solana',        'base': 'SOL',  'icon': '◎'},
    'ADA-USD':  {'name': 'Cardano',       'base': 'ADA',  'icon': '₳'},
    'DOGE-USD': {'name': 'Dogecoin',      'base': 'DOGE', 'icon': 'Ð'},
    'XRP-USD':  {'name': 'XRP',           'base': 'XRP',  'icon': '✕'},
    'BCH-USD':  {'name': 'Bitcoin Cash',  'base': 'BCH',  'icon': '₿'},
    'LINK-USD': {'name': 'Chainlink',     'base': 'LINK', 'icon': '⬡'},
    'AVAX-USD': {'name': 'Avalanche',     'base': 'AVAX', 'icon': '△'},
    'DOT-USD':  {'name': 'Polkadot',      'base': 'DOT',  'icon': '●'},
    'MATIC-USD':{'name': 'Polygon',       'base': 'MATIC','icon': '⬟'},
    'UNI-USD':  {'name': 'Uniswap',       'base': 'UNI',  'icon': '🦄'},
    'ATOM-USD': {'name': 'Cosmos',        'base': 'ATOM', 'icon': '⚛'},
    'NEAR-USD': {'name': 'NEAR Protocol', 'base': 'NEAR', 'icon': '◈'},
}

COINBASE_API = 'https://api.exchange.coinbase.com'
HEADERS = {
    'User-Agent': 'FIX-Support-AI-Engine/1.0',
    'Accept':     'application/json',
}


# ─────────────────────────────────────────────────────────────────────────────
# HTTP HELPER
# ─────────────────────────────────────────────────────────────────────────────

def fetch_json(url):
    """Fetch a JSON endpoint and return the parsed dict."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Coinbase API HTTP {e.code}: {body}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'Network error reaching Coinbase API: {e.reason}')


# ─────────────────────────────────────────────────────────────────────────────
# MARKET DATA FETCHER
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(value, default=0.0):
    try:
        return float(value) if value else default
    except (TypeError, ValueError):
        return default


def _fmt_price(value):
    """Format a price with appropriate decimal places."""
    if value >= 1000:
        return f'{value:,.2f}'
    elif value >= 1:
        return f'{value:,.4f}'
    else:
        return f'{value:,.6f}'


def get_market_data(product_id):
    """
    Fetch ticker + 24hr stats for a product from Coinbase Exchange.
    Returns a clean dict ready to JSON-serialise.
    """
    product_id = product_id.upper().strip()
    if product_id not in SUPPORTED_PRODUCTS:
        raise ValueError(
            f"'{product_id}' is not a supported product. "
            f"Supported: {', '.join(sorted(SUPPORTED_PRODUCTS.keys()))}"
        )

    meta = SUPPORTED_PRODUCTS[product_id]

    # ── Ticker (bid / ask / last price / volume) ──────────────────────────────
    ticker = fetch_json(f'{COINBASE_API}/products/{product_id}/ticker')

    bid    = _safe_float(ticker.get('bid'))
    ask    = _safe_float(ticker.get('ask'))
    last   = _safe_float(ticker.get('price'))
    volume = _safe_float(ticker.get('volume'))
    ts     = ticker.get('time', datetime.now(timezone.utc).isoformat())

    # ── 24hr Stats (open / high / low) ────────────────────────────────────────
    stats = fetch_json(f'{COINBASE_API}/products/{product_id}/stats')

    open_24h = _safe_float(stats.get('open'))
    high_24h = _safe_float(stats.get('high'))
    low_24h  = _safe_float(stats.get('low'))

    # ── Derived metrics ───────────────────────────────────────────────────────
    spread     = ask - bid
    spread_pct = round((spread / bid * 100), 4) if bid > 0 else 0.0
    mid        = (bid + ask) / 2 if (bid and ask) else last

    change_24h     = round(last - open_24h, 8) if open_24h else 0.0
    change_pct_24h = round((change_24h / open_24h * 100), 2) if open_24h else 0.0

    range_24h     = high_24h - low_24h
    range_pct_24h = round((range_24h / low_24h * 100), 2) if low_24h else 0.0

    # Distance from current price to 24hr high/low
    pct_from_high = round(((last - high_24h) / high_24h * 100), 2) if high_24h else 0.0
    pct_from_low  = round(((last - low_24h)  / low_24h  * 100), 2) if low_24h  else 0.0

    return {
        # Identity
        'product_id':   product_id,
        'name':         meta['name'],
        'base':         meta['base'],
        'quote':        'USD',
        'icon':         meta['icon'],

        # Core prices
        'bid':          bid,
        'ask':          ask,
        'last':         last,
        'mid':          round(mid, 8),
        'open_24h':     open_24h,
        'high_24h':     high_24h,
        'low_24h':      low_24h,
        'volume_24h':   round(volume, 4),

        # Spread
        'spread':       round(spread, 8),
        'spread_pct':   spread_pct,

        # 24hr change
        'change_24h':     change_24h,
        'change_pct_24h': change_pct_24h,
        'direction':      'up' if change_24h >= 0 else 'down',

        # Range
        'range_24h':      round(range_24h, 8),
        'range_pct_24h':  range_pct_24h,
        'pct_from_high':  pct_from_high,
        'pct_from_low':   pct_from_low,

        # Formatted strings for direct display
        'fmt_bid':    _fmt_price(bid),
        'fmt_ask':    _fmt_price(ask),
        'fmt_last':   _fmt_price(last),
        'fmt_spread': _fmt_price(spread),
        'fmt_high':   _fmt_price(high_24h),
        'fmt_low':    _fmt_price(low_24h),
        'fmt_volume': f'{volume:,.4f}',

        # Timestamp
        'timestamp':    ts,
        'fetched_at':   datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
    }


# ─────────────────────────────────────────────────────────────────────────────
# LAMBDA HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def _resp(code, body_dict):
    return {
        'statusCode': code,
        'headers': {
            'Content-Type':                'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers':'Content-Type',
            'Access-Control-Allow-Methods':'GET,OPTIONS',
        },
        'body': json.dumps(body_dict),
    }


def lambda_handler(event, context):
    try:
        method = event.get('requestContext', {}).get('http', {}).get('method', '')
        if method == 'OPTIONS':
            return _resp(200, {})

        # Product from query string: GET /market-data?product=BTC-USD
        qs      = event.get('queryStringParameters') or {}
        product = (qs.get('product') or 'BTC-USD').strip().upper()

        data = get_market_data(product)
        return _resp(200, data)

    except ValueError as e:
        return _resp(400, {'error': str(e), 'supported': list(SUPPORTED_PRODUCTS.keys())})
    except RuntimeError as e:
        return _resp(502, {'error': f'Coinbase API error: {str(e)}'})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return _resp(500, {'error': f'Unexpected error: {str(e)}'})