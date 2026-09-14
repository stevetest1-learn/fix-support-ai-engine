"""
FIX Support AI Engine — Crypto Venue Dialect Extensions

Provides tag overlay dictionaries and venue detection for three
crypto FIX venues: Coinbase Advanced Trade, Kraken, and Talos.

Each venue sits on a standard FIX 4.2 base but adds proprietary
custom tags (7928+, 9001+, 1300+ range) and venue-specific
enumerated values. This module detects the venue from the parsed
message and merges the appropriate extensions into the base
dictionary so the parser can decode every tag correctly.
"""

import re

# ─────────────────────────────────────────────────────────────────────────────
# KNOWN CRYPTO BASE CURRENCY SYMBOLS
# Used to detect crypto symbol format from tag 55
# ─────────────────────────────────────────────────────────────────────────────
CRYPTO_BASE_SYMBOLS = {
    'BTC', 'XBT',   # Bitcoin (Coinbase uses BTC, Kraken uses XBT)
    'ETH',           # Ethereum
    'SOL',           # Solana
    'ADA',           # Cardano
    'DOGE',          # Dogecoin
    'XRP',           # Ripple
    'LTC',           # Litecoin
    'BCH',           # Bitcoin Cash
    'LINK',          # Chainlink
    'UNI',           # Uniswap
    'AVAX',          # Avalanche
    'MATIC', 'POL',  # Polygon
    'DOT',           # Polkadot
    'ATOM',          # Cosmos
    'ALGO',          # Algorand
    'FIL',           # Filecoin
    'AAVE',          # Aave
    'COMP',          # Compound
    'MKR',           # Maker
    'SNX',           # Synthetix
    'CRV',           # Curve
    'GRT',           # The Graph
    'NEAR',          # Near Protocol
    'FTM',           # Fantom
    'XLM',           # Stellar
    'XMR',           # Monero
    'ZEC',           # Zcash
    'DASH',          # Dash
    'ETC',           # Ethereum Classic
    'SHIB',          # Shiba Inu
    'APE',           # ApeCoin
    'OP',            # Optimism
    'ARB',           # Arbitrum
    'SUI',           # Sui
    'APT',           # Aptos
    'PEPE',          # Pepe
    'TON',           # Toncoin
    'USDT', 'USDC', 'DAI', 'BUSD', 'TUSD',  # Stablecoins
}

FIAT_QUOTE_SYMBOLS = {'USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'USDT', 'USDC', 'BTC', 'ETH'}

# CompID name fragments that identify each venue (case-insensitive)
VENUE_COMPID_HINTS = {
    'Coinbase':   ['COINBASE', 'CBADV', 'CBPRO', 'GDAX', 'CB-'],
    'Kraken':     ['KRAKEN', 'KRAKENFIX', 'KRK'],
    'Talos':      ['TALOS'],
    'Gemini':     ['GEMINI', 'GEM-'],
    'Bitstamp':   ['BITSTAMP', 'BSTAMP'],
    'OKX':        ['OKX', 'OKEX'],
    'B2C2':       ['B2C2'],
    'Cumberland': ['CUMBERLAND', 'CLD-'],
    'Galaxy':     ['GALAXY', 'GAL-'],
    'Wintermute': ['WINTERMUTE', 'WMT-'],
    'Deribit':    ['DERIBIT'],
}


# ─────────────────────────────────────────────────────────────────────────────
# COINBASE ADVANCED TRADE — FIX 4.2 DIALECT
# Source: Coinbase Advanced Trade FIX API documentation
# ─────────────────────────────────────────────────────────────────────────────
COINBASE_TAGS = {
    # ── Core Coinbase custom tags ────────────────────────────────────────────
    "7928": (
        "SelfTradePrevention",
        "Coinbase: Controls behaviour when a resting order would match against "
        "an order from the same account",
        {
            "DC": "Decrease and Cancel — reduce the new order to prevent a match; cancel if size reaches zero",
            "CO": "Cancel Oldest — cancel the resting order and allow the new order to proceed",
            "CN": "Cancel Newest — cancel the incoming order that would cause the match",
            "CB": "Cancel Both — cancel both the resting and the incoming order",
            "N":  "None — no self-trade prevention; allow the trade to execute",
        }
    ),
    "8013": (
        "CancelAfter",
        "Coinbase: Automatically cancel the order after the specified period. "
        "Only valid for GTD TimeInForce (59=6)",
        {
            "min": "Cancel after 1 minute",
            "hour": "Cancel after 1 hour",
            "GTD": "Cancel at the date/time specified in ExpireTime (tag 126)",
        }
    ),
    "8014": (
        "PostOnly",
        "Coinbase: When Y, the order will only be placed if it would rest on the book "
        "as a maker (no taker fill). If it would immediately fill, the order is rejected.",
        {
            "Y": "Post only — maker orders only; rejected if it would take liquidity",
            "N": "Standard order — may take liquidity",
        }
    ),
    "9001": (
        "CoinbaseOrderID",
        "Coinbase: Internal Coinbase order identifier returned on Execution Reports. "
        "This is the Coinbase system-assigned order UUID, distinct from tag 37 (OrderID).",
        {}
    ),
    "9002": (
        "CoinbaseProductID",
        "Coinbase: The Coinbase product identifier string (e.g. BTC-USD). "
        "Corresponds to tag 55 (Symbol) but uses the Coinbase canonical product format.",
        {}
    ),
    "9003": (
        "CoinbaseFillPrice",
        "Coinbase: The exact fill price for this execution, expressed as a string "
        "in the product's quote currency. May differ from LastPx (tag 31) due to rounding.",
        {}
    ),
    "9004": (
        "CoinbaseFillSize",
        "Coinbase: The exact fill size for this execution in base currency units. "
        "May differ from LastShares (tag 32) due to precision handling.",
        {}
    ),
    "1138": (
        "DisplayQty",
        "Coinbase: Visible quantity for an iceberg order. "
        "The order rests on the book showing only this quantity; the remainder is hidden.",
        {}
    ),
    # ── Coinbase-extended standard tag values ─────────────────────────────────
    # Tag 40 OrdType — Coinbase supports Stop and Stop-Limit natively
    # Tag 59 TimeInForce — Coinbase supports Day, GTC, IOC, FOK, GTD
    # Tag 54 Side — standard Buy/Sell only (1 and 2)
}

COINBASE_DESCRIPTIONS = {
    "7928": "Coinbase: Self-trade prevention — controls what happens when your order would match your own resting order",
    "8013": "Coinbase: Auto-cancel timer — cancels the order after min / hour / GTD expiry",
    "8014": "Coinbase: Post-only flag — ensures the order is a maker order; rejected if it would take",
    "9001": "Coinbase: Coinbase internal order UUID (complements standard tag 37 OrderID)",
    "9002": "Coinbase: Coinbase product identifier string, e.g. BTC-USD",
    "9003": "Coinbase: Exact fill price in quote currency (full precision)",
    "9004": "Coinbase: Exact fill size in base currency (full precision)",
    "1138": "Coinbase: Iceberg order visible quantity — only this amount shows on the order book",
}


# ─────────────────────────────────────────────────────────────────────────────
# KRAKEN — FIX 4.2 DIALECT
# Source: Kraken FIX API documentation
# Note: Kraken uses XBT (not BTC) for Bitcoin in all symbol pairs
# ─────────────────────────────────────────────────────────────────────────────
KRAKEN_TAGS = {
    "9001": (
        "KrakenOrderType",
        "Kraken: Extended order type field used alongside tag 40 (OrdType) for "
        "crypto-specific order types not covered by the FIX 4.2 standard",
        {
            "stop-loss":        "Stop Loss — market order triggered when price falls to StopPx (tag 99)",
            "take-profit":      "Take Profit — market order triggered when price rises to StopPx (tag 99)",
            "stop-loss-limit":  "Stop Loss Limit — limit order triggered at StopPx",
            "take-profit-limit":"Take Profit Limit — limit order triggered at StopPx",
            "trailing-stop":    "Trailing Stop — stop price trails the market by a fixed offset",
            "trailing-stop-limit": "Trailing Stop Limit — limit order with trailing stop trigger",
            "settle-position":  "Settle Position — immediately settle/close an open leveraged position",
        }
    ),
    "9002": (
        "KrakenLeverage",
        "Kraken: Desired leverage ratio for a margin order. "
        "Valid values are 2, 3, 4, or 5 (expressed as a string: '2:1', '5:1' etc.).",
        {}
    ),
    "9003": (
        "KrakenStopDirection",
        "Kraken: Specifies the trigger direction for a stop or take-profit order",
        {
            "up":   "Trigger when price moves UP to or above the stop price",
            "down": "Trigger when price moves DOWN to or at or below the stop price",
        }
    ),
    "9004": (
        "KrakenTrailingOffset",
        "Kraken: Trailing stop offset value. "
        "Expressed as an absolute price amount or a percentage (appended with %).",
        {}
    ),
    "9005": (
        "KrakenStartTime",
        "Kraken: Scheduled start time for the order. Order will not be placed before this time. "
        "Format: Unix timestamp or RFC3339 datetime string.",
        {}
    ),
    "9006": (
        "KrakenExpireTime",
        "Kraken: Absolute expiry time for the order (overrides tag 126 ExpireTime). "
        "Format: Unix timestamp or RFC3339 datetime string.",
        {}
    ),
    "9007": (
        "KrakenUserRef",
        "Kraken: Optional user-supplied integer reference number for an order. "
        "Useful for grouping orders or correlating with an external system.",
        {}
    ),
    "9008": (
        "KrakenFee",
        "Kraken: Actual fee charged for this execution, expressed in the quote currency. "
        "Returned on Execution Reports (35=8).",
        {}
    ),
    "9009": (
        "KrakenFeeCurrency",
        "Kraken: Currency in which the fee (tag 9008) was charged. "
        "Typically the quote currency of the pair (e.g. USD for XBT/USD).",
        {}
    ),
    "9010": (
        "KrakenVolumeCurrency",
        "Kraken: Specifies whether OrderQty (tag 38) is expressed in the base "
        "or the quote currency of the pair.",
        {
            "base":  "OrderQty is in base currency (e.g. XBT for XBT/USD)",
            "quote": "OrderQty is in quote currency (e.g. USD for XBT/USD)",
        }
    ),
    "9011": (
        "KrakenPostTrade",
        "Kraken: Post-trade settlement details or position reference. "
        "Returned on fills for leveraged/margin accounts.",
        {}
    ),
    "6001": (
        "KrakenCloseOrderType",
        "Kraken: Order type for the automatic close-position order placed when a "
        "leveraged position is opened. Mirrors the values of tag 40 (OrdType).",
        {
            "limit":  "Limit — close position with a limit order at the specified price",
            "stop-loss":  "Stop Loss — close position with a stop-loss market order",
            "take-profit": "Take Profit — close position with a take-profit market order",
        }
    ),
}

KRAKEN_DESCRIPTIONS = {
    "9001": "Kraken: Extended order type for stop-loss, take-profit and trailing stop orders",
    "9002": "Kraken: Leverage ratio for margin orders (2:1 through 5:1)",
    "9003": "Kraken: Stop/take-profit trigger direction (up or down)",
    "9004": "Kraken: Trailing stop price offset — absolute amount or percentage",
    "9005": "Kraken: Scheduled order start time (Unix timestamp or RFC3339)",
    "9006": "Kraken: Absolute order expiry time (overrides tag 126)",
    "9007": "Kraken: User-supplied integer reference number for order grouping",
    "9008": "Kraken: Actual fee charged in quote currency for this fill",
    "9009": "Kraken: Currency in which the fee was charged",
    "9010": "Kraken: Specifies whether OrderQty is in base or quote currency",
    "9011": "Kraken: Post-trade settlement or position reference for margin fills",
    "6001": "Kraken: Auto close-position order type when a leveraged trade is opened",
}


# ─────────────────────────────────────────────────────────────────────────────
# TALOS — FIX 4.4 / FIX 4.2 DIALECT
# Talos is a multi-venue crypto trading platform providing smart order routing
# across spot, derivatives and OTC venues. It wraps FIX with venue routing tags.
# Source: Talos FIX API and public documentation
# ─────────────────────────────────────────────────────────────────────────────
TALOS_TAGS = {
    "1301": (
        "MarketID",
        "Talos: Target execution venue identifier. "
        "Tells the Talos SOR which downstream crypto exchange to route the order to.",
        {
            "COINBASE":  "Coinbase Advanced Trade (formerly Coinbase Pro)",
            "KRAKEN":    "Kraken spot exchange",
            "GEMINI":    "Gemini exchange",
            "BITSTAMP":  "Bitstamp exchange",
            "BINANCE":   "Binance spot exchange",
            "BINANCEUS": "Binance.US",
            "BITFINEX":  "Bitfinex exchange",
            "HUOBI":     "Huobi / HTX",
            "OKX":       "OKX exchange",
            "BYBIT":     "Bybit exchange",
            "KUCOIN":    "KuCoin exchange",
            "DERIBIT":   "Deribit derivatives exchange",
            "CUMBERLAND":"Cumberland DRW OTC desk",
            "B2C2":      "B2C2 OTC desk",
            "GALAXY":    "Galaxy Digital OTC desk",
            "WINTERMUTE":"Wintermute Trading",
        }
    ),
    "7001": (
        "TalosAlgorithm",
        "Talos: Smart order routing algorithm or execution strategy to apply. "
        "Controls how the Talos platform splits and routes the order across venues.",
        {
            "TWAP":   "Time-Weighted Average Price — split order evenly over the time window",
            "VWAP":   "Volume-Weighted Average Price — size slices proportional to market volume",
            "SOR":    "Smart Order Router — route to best available price across connected venues",
            "SNIPER": "Sniper — aggressive immediate execution at best available price",
            "ICEBERG":"Iceberg — show only a portion of the total order size on each venue",
            "POV":    "Percentage of Volume — participate at a target fraction of market volume",
            "IS":     "Implementation Shortfall — minimize market impact vs arrival price",
        }
    ),
    "7002": (
        "TalosStrategyParam",
        "Talos: Free-text parameter string passed to the execution algorithm "
        "specified in TalosAlgorithm (tag 7001). Format is algorithm-specific.",
        {}
    ),
    "7003": (
        "TalosVenueOrderID",
        "Talos: The order ID assigned by the downstream execution venue "
        "(e.g. Coinbase order UUID, Kraken txid). "
        "Complements the Talos-assigned OrderID in tag 37.",
        {}
    ),
    "7004": (
        "TalosVenueExecID",
        "Talos: The execution/fill ID assigned by the downstream venue. "
        "Complements the Talos-assigned ExecID in tag 17.",
        {}
    ),
    "7005": (
        "TalosSlippageBps",
        "Talos: Maximum acceptable slippage in basis points from the reference price. "
        "Orders will reject if execution would exceed this threshold.",
        {}
    ),
    "7006": (
        "TalosParticipationRate",
        "Talos: Target participation rate as a percentage (0.0 to 1.0) for POV "
        "algorithm (TalosAlgorithm=POV). Example: 0.10 = 10% of market volume.",
        {}
    ),
    "7007": (
        "TalosBenchmarkPrice",
        "Talos: Reference benchmark price used by execution algorithms for "
        "performance measurement and limit checking.",
        {}
    ),
    "7008": (
        "TalosRoutingPolicy",
        "Talos: Specifies the cross-venue routing priority policy",
        {
            "PRICE":    "Route to the venue offering the best price",
            "SPEED":    "Route to the fastest venue regardless of minor price differences",
            "BALANCED": "Balance price improvement against execution speed",
            "SINGLE":   "Route entire order to one venue (no splitting)",
            "SPREAD":   "Spread order proportionally across all connected venues",
        }
    ),
    "7009": (
        "TalosLegVenue",
        "Talos: For multi-leg or spread orders, specifies the execution venue "
        "for an individual leg. Used in conjunction with standard leg tags.",
        {}
    ),
    "7010": (
        "TalosAccountGroup",
        "Talos: Account group identifier used for portfolio-level allocation "
        "and P&L attribution across sub-accounts.",
        {}
    ),
    "7011": (
        "TalosNettingGroup",
        "Talos: Netting group identifier. Orders in the same group may be "
        "netted against each other before routing to reduce market impact.",
        {}
    ),
    "7012": (
        "TalosOrderSource",
        "Talos: Identifies the originating system or channel that placed the order",
        {
            "API":        "Direct API connection",
            "GUI":        "Talos Trading GUI",
            "ALGO":       "Algorithmic / automated strategy",
            "RISK":       "Risk management system",
            "VOICE":      "Voice-brokered order entered manually",
        }
    ),
    "7013": (
        "TalosPortfolioID",
        "Talos: Portfolio identifier for multi-portfolio account structures. "
        "Used for order attribution and position tracking.",
        {}
    ),
    "7014": (
        "TalosVenueExecVenue",
        "Talos: Name of the actual venue where a fill occurred, returned on "
        "Execution Reports. Useful when Talos SOR split an order across venues.",
        {}
    ),
    "7015": (
        "TalosExecFee",
        "Talos: Execution fee charged by the venue for this fill, expressed "
        "in the quote currency. Returned on Execution Reports (35=8).",
        {}
    ),
    "7016": (
        "TalosExecFeeCurrency",
        "Talos: Currency of the execution fee in tag 7015.",
        {}
    ),
    "7017": (
        "TalosMakerTaker",
        "Talos: Indicates whether this fill was a maker or taker execution",
        {
            "MAKER": "Order rested on the book and was filled by an aggressor — maker fee applies",
            "TAKER": "Order took liquidity from a resting order — taker fee applies",
        }
    ),
    "7018": (
        "TalosSettlementType",
        "Talos: Settlement method for the executed trade",
        {
            "SPOT":    "Spot settlement — standard T+N depending on venue",
            "INSTANT": "Instant settlement — venue-specific near-instant settlement",
            "DELAYED": "Delayed settlement — custom settlement date",
        }
    ),
}

TALOS_DESCRIPTIONS = {
    "1301": "Talos: Target execution venue — tells the Talos SOR which crypto exchange to route to",
    "7001": "Talos: Execution algorithm (TWAP, VWAP, SOR, Iceberg, POV, etc.)",
    "7002": "Talos: Algorithm parameter string — format depends on the chosen algorithm",
    "7003": "Talos: Downstream venue order ID (Coinbase UUID, Kraken txid, etc.)",
    "7004": "Talos: Downstream venue execution/fill ID",
    "7005": "Talos: Maximum acceptable slippage in basis points from reference price",
    "7006": "Talos: POV algorithm participation rate (0.0 to 1.0)",
    "7007": "Talos: Reference benchmark price for algorithm performance measurement",
    "7008": "Talos: Cross-venue routing priority policy (PRICE / SPEED / BALANCED / etc.)",
    "7009": "Talos: Execution venue for an individual leg of a multi-leg order",
    "7010": "Talos: Account group for portfolio allocation and P&L attribution",
    "7011": "Talos: Netting group — orders in the same group may be netted before routing",
    "7012": "Talos: Order origin channel (API / GUI / ALGO / RISK / VOICE)",
    "7013": "Talos: Portfolio identifier for multi-portfolio account structures",
    "7014": "Talos: Actual venue where fill occurred (returned on Execution Reports)",
    "7015": "Talos: Venue execution fee in quote currency for this fill",
    "7016": "Talos: Currency of the execution fee (tag 7015)",
    "7017": "Talos: Maker/Taker indicator for fee tier determination",
    "7018": "Talos: Settlement type (SPOT / INSTANT / DELAYED)",
}


# ─────────────────────────────────────────────────────────────────────────────
# DIALECT REGISTRY
# ─────────────────────────────────────────────────────────────────────────────
DIALECTS = {
    'Coinbase': {
        'tags':         COINBASE_TAGS,
        'descriptions': COINBASE_DESCRIPTIONS,
        'color':        '#0052ff',   # Coinbase brand blue (for frontend badge)
        'fix_version':  'FIX.4.2',
    },
    'Kraken':   {
        'tags':         KRAKEN_TAGS,
        'descriptions': KRAKEN_DESCRIPTIONS,
        'color':        '#5741d9',   # Kraken brand purple
        'fix_version':  'FIX.4.2',
    },
    'Talos':    {
        'tags':         TALOS_TAGS,
        'descriptions': TALOS_DESCRIPTIONS,
        'color':        '#00c2a8',   # Talos teal
        'fix_version':  'FIX.4.2 / FIX.4.4',
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# VENUE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def _symbol_is_crypto(symbol):
    """Return True if symbol looks like a crypto trading pair."""
    if not symbol:
        return False
    symbol = symbol.upper()
    # Coinbase format: BTC-USD, ETH-EUR
    m = re.match(r'^([A-Z0-9]+)[/\-]([A-Z0-9]+)$', symbol)
    if m:
        base, quote = m.group(1), m.group(2)
        if base in CRYPTO_BASE_SYMBOLS and quote in FIAT_QUOTE_SYMBOLS:
            return True
    # Bare crypto symbol with no quote (some venues use just 'XBT' in tag 55)
    if symbol in CRYPTO_BASE_SYMBOLS:
        return True
    return False


def _coinbase_symbol(symbol):
    """Coinbase uses BASE-QUOTE with a hyphen and BTC (not XBT)."""
    if not symbol:
        return False
    return bool(re.match(r'^[A-Z0-9]+-[A-Z0-9]+$', symbol.upper()))


def _kraken_symbol(symbol):
    """Kraken uses BASE/QUOTE with a slash and XBT for Bitcoin."""
    if not symbol:
        return False
    sym = symbol.upper()
    if 'XBT' in sym:
        return True
    return bool(re.match(r'^[A-Z0-9]+/[A-Z0-9]+$', sym))


def detect_venue(pairs_dict):
    """
    Detect which crypto venue (if any) sent this FIX message.

    Detection priority:
    1. Presence of a venue-exclusive custom tag (strongest signal)
    2. SenderCompID / TargetCompID pattern match
    3. Symbol format (hyphen = Coinbase, slash/XBT = Kraken)
    4. Venue-specific tag range presence

    Returns (venue_name: str | None, signals: list[str])
    """
    signals = []

    sender = (pairs_dict.get('49') or '').upper()
    target = (pairs_dict.get('56') or '').upper()
    symbol = pairs_dict.get('55') or ''

    all_tags = set(pairs_dict.keys())

    # ── Signal 1: Venue-exclusive tags ───────────────────────────────────────
    if '7928' in all_tags or '8013' in all_tags or '8014' in all_tags:
        signals.append('Coinbase-exclusive tag detected (7928 / 8013 / 8014)')
        return 'Coinbase', signals

    if '7001' in all_tags or '7003' in all_tags or '7008' in all_tags or '1301' in all_tags:
        signals.append('Talos-exclusive tag detected (7001 / 7003 / 7008 / 1301)')
        return 'Talos', signals

    # ── Signal 2: CompID name patterns ───────────────────────────────────────
    for venue, hints in VENUE_COMPID_HINTS.items():
        for hint in hints:
            if hint in sender or hint in target:
                signals.append(f'CompID match: "{hint}" found in SenderCompID/TargetCompID')
                # Only map the three main dialects; others are informational
                if venue in DIALECTS:
                    return venue, signals
                else:
                    return venue, signals   # return name even without full dialect

    # ── Signal 3: Symbol format ───────────────────────────────────────────────
    if symbol:
        if _kraken_symbol(symbol):
            signals.append(f'Symbol "{symbol}" matches Kraken format (slash-delimited or XBT)')
            return 'Kraken', signals
        if _coinbase_symbol(symbol):
            signals.append(f'Symbol "{symbol}" matches Coinbase format (hyphen-delimited)')
            return 'Coinbase', signals
        if _symbol_is_crypto(symbol):
            signals.append(f'Symbol "{symbol}" identified as crypto but venue is ambiguous')
            return 'Crypto (venue undetected)', signals

    # ── Signal 4: Kraken tag range (9001–9011) ────────────────────────────────
    kraken_range = {'9001','9002','9003','9004','9005','9006','9007','9008','9009','9010','9011'}
    if all_tags & kraken_range:
        signals.append('Kraken custom tag range (9001-9011) detected')
        return 'Kraken', signals

    return None, signals


# ─────────────────────────────────────────────────────────────────────────────
# DICTIONARY OVERLAY
# ─────────────────────────────────────────────────────────────────────────────

def apply_dialect(dictionary, venue_name):
    """
    Merge venue-specific tag extensions into a base FIX dictionary.
    The base dictionary is not mutated — a shallow copy is returned.

    If the venue has no registered dialect (e.g. 'Gemini', 'Bitstamp')
    the original dictionary is returned unchanged.
    """
    if venue_name not in DIALECTS:
        return dictionary

    dialect = DIALECTS[venue_name]
    venue_tags  = dialect['tags']
    venue_descs = dialect['descriptions']

    # Shallow copy the top-level dict and the fields/descriptions sub-dicts
    merged = dict(dictionary)
    merged['fields']             = dict(dictionary.get('fields', {}))
    merged['field_descriptions'] = dict(dictionary.get('field_descriptions', {}))

    for tag, (field_name, description, values) in venue_tags.items():
        # field tuple: (field_name, ftype, enum_values_dict)
        merged['fields'][tag] = (field_name, 'String', values)
        merged['field_descriptions'][tag] = description

    return merged