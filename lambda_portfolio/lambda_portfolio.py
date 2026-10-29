"""
FIX Support AI Engine — Portfolio Analyser Lambda

Accepts an uploaded file and returns a portfolio support analysis.
Two input formats are auto-detected:

  1. Holdings CSV  — one row per position:
       account,symbol,asset_class,currency,quantity,avg_cost,market_price
     (column names are matched case-insensitively; common aliases accepted)

  2. FIX log       — 35=8 Execution Reports with fills (150=F or 150=1/2).
     Positions are built from the fills (1=Account, 55=Symbol, 54=Side,
     32=LastQty, 31=LastPx, 15=Currency). Last fill price is used as the mark.

Returns:
  - summary            firm totals in the base currency (MV, gross, long/short, P&L)
  - fx                 rate used for every currency and where it came from
  - accounts           per-account roll-up in base currency, top holding, share of firm
  - symbols            per-symbol roll-up across all accounts
  - exposure           roll-up by asset class and by currency
  - positions          every position in local and base currency, with weight
  - alerts             concentration, large loss, missing FX rate, data quality

FX: every position is converted to base_currency (default USD) so totals roll
up across accounts and currencies. Rates come from the request (fx_rates, wins)
or live from the Coinbase public exchange-rates API. Pure Python stdlib.

"""

import base64
import csv
import io
import json
import re
import urllib.request
from collections import defaultdict

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — tune these support thresholds to your desk's risk limits
# ─────────────────────────────────────────────────────────────────────────────
CONCENTRATION_LIMIT_PCT = 25.0   # single position > X% of its account
LOSS_ALERT_PCT          = -10.0  # unrealised loss worse than X% of cost

COLUMN_ALIASES = {
    "account":      ["account", "acct", "portfolio", "fund", "book"],
    "symbol":       ["symbol", "ticker", "instrument", "security", "ccypair"],
    "asset_class":  ["asset_class", "assetclass", "asset class", "class", "type"],
    "currency":     ["currency", "ccy", "curr"],
    "quantity":     ["quantity", "qty", "position", "shares", "units", "notional"],
    "avg_cost":     ["avg_cost", "avgcost", "cost", "avg_price", "book_price", "cost_price"],
    "market_price": ["market_price", "price", "mark", "last", "close", "mkt_price"],
}
REQUIRED = ["account", "symbol", "quantity", "avg_cost", "market_price"]


# ─────────────────────────────────────────────────────────────────────────────
# PARSING
# ─────────────────────────────────────────────────────────────────────────────
def _num(val):
    if val is None:
        return None
    s = str(val).strip().replace(",", "").replace("$", "")
    if s.startswith("(") and s.endswith(")"):        # accounting negatives
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def looks_like_fix(text):
    return bool(re.search(r"\b8=FIX", text)) and "35=" in text


def parse_holdings_csv(text, issues):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("File has no header row.")

    header_map = {}
    lower = {h.strip().lower(): h for h in reader.fieldnames if h}
    for field, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a in lower:
                header_map[field] = lower[a]
                break
    missing = [f for f in REQUIRED if f not in header_map]
    if missing:
        raise ValueError(
            "Missing required column(s): " + ", ".join(missing)
            + ". Found: " + ", ".join(reader.fieldnames)
        )

    positions, seen = [], set()
    for line_no, row in enumerate(reader, start=2):
        get = lambda f: (row.get(header_map[f]) or "").strip() if f in header_map else ""
        account, symbol = get("account"), get("symbol").upper()
        if not account and not symbol:
            continue
        qty, cost, px = _num(get("quantity")), _num(get("avg_cost")), _num(get("market_price"))

        if not account or not symbol:
            issues.append({"line": line_no, "issue": "Missing account or symbol"})
            continue
        if qty is None:
            issues.append({"line": line_no, "symbol": symbol, "issue": "Quantity is not numeric"})
            continue
        if px is None or px <= 0:
            issues.append({"line": line_no, "symbol": symbol, "issue": "Missing or non-positive market price — position not marked"})
        if cost is None:
            issues.append({"line": line_no, "symbol": symbol, "issue": "Missing average cost — P&L not computed"})
        key = (account, symbol)
        if key in seen:
            issues.append({"line": line_no, "symbol": symbol, "issue": f"Duplicate position for account {account}"})
        seen.add(key)

        positions.append({
            "account": account, "symbol": symbol,
            "asset_class": get("asset_class") or "Unclassified",
            "currency": (get("currency") or "USD").upper(),
            "quantity": qty, "avg_cost": cost, "market_price": px,
        })
    return positions


def parse_fix_fills(text, issues):
    """Build positions from 35=8 fills. Buys add, sells reduce (avg-cost method)."""
    book = {}
    fills = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        m = re.search(r"8=FIX.*", line)
        if not m:
            continue
        raw = m.group(0)
        sep = "\x01" if "\x01" in raw else "|"
        tags = dict(p.split("=", 1) for p in raw.split(sep) if "=" in p)
        if tags.get("35") != "8" or tags.get("150") not in ("F", "1", "2"):
            continue
        qty, px = _num(tags.get("32")), _num(tags.get("31"))
        symbol, side = tags.get("55", "").upper(), tags.get("54")
        account = tags.get("1") or "NO_ACCOUNT"
        if not symbol or qty is None or px is None or side not in ("1", "2", "5", "6"):
            issues.append({"line": line_no, "issue": "Fill missing 55/54/32/31 — skipped"})
            continue
        if "1" not in tags:
            issues.append({"line": line_no, "symbol": symbol, "issue": "Fill has no Account (1) — settlement break risk"})
        fills += 1
        key = (account, symbol)
        p = book.setdefault(key, {"account": account, "symbol": symbol,
                                  "asset_class": "Unclassified",
                                  "currency": tags.get("15", "USD").upper(),
                                  "quantity": 0.0, "cost_total": 0.0, "market_price": px})
        signed = qty if side == "1" else -qty
        if p["quantity"] == 0 or (p["quantity"] > 0) == (signed > 0):
            p["cost_total"] += signed * px                      # adding to position
        else:
            closing = min(abs(signed), abs(p["quantity"]))
            avg = p["cost_total"] / p["quantity"]
            p["cost_total"] -= closing * avg * (1 if p["quantity"] > 0 else -1)
            if abs(signed) > closing:                          # flipped through zero
                p["cost_total"] = (signed + (closing if signed < 0 else -closing)) * px
        p["quantity"] += signed
        p["market_price"] = px
    positions = []
    for p in book.values():
        q = p["quantity"]
        p["avg_cost"] = (p.pop("cost_total") / q) if q else None
        if q == 0:
            continue                                           # flat — drop
        positions.append(p)
    return positions, fills


# ─────────────────────────────────────────────────────────────────────────────
# FX RATES — convert everything to one base currency so totals roll up
# ─────────────────────────────────────────────────────────────────────────────
FX_API = "https://api.coinbase.com/v2/exchange-rates?currency={base}"   # public, no key


def fetch_live_rates(base):
    """Return {CCY: units of BASE per 1 CCY}. Empty dict on any failure."""
    try:
        req = urllib.request.Request(FX_API.format(base=base),
                                     headers={"User-Agent": "fix-support-ai-engine"})
        with urllib.request.urlopen(req, timeout=6) as r:
            rates = json.loads(r.read().decode())["data"]["rates"]
        out = {}
        for ccy, per_base in rates.items():          # 1 BASE = per_base CCY
            v = _num(per_base)
            if v:
                out[ccy.upper()] = 1.0 / v
        return out
    except Exception:
        return {}


def resolve_rates(currencies, base, manual, use_live):
    """Manual rates win, then live, then 1.0 for the base itself."""
    rates, sources, missing = {}, {}, []
    live = fetch_live_rates(base) if use_live and any(c != base for c in currencies) else {}
    for c in sorted(currencies):
        if c == base:
            rates[c], sources[c] = 1.0, "base"
        elif _num(manual.get(c)) and _num(manual.get(c)) > 0:
            rates[c], sources[c] = _num(manual[c]), "manual"
        elif c in live:
            rates[c], sources[c] = live[c], "live"
        else:
            missing.append(c)
    return rates, sources, missing


# ─────────────────────────────────────────────────────────────────────────────
# ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────
def _r(x, n=2):
    return round(x, n) if x is not None else None


def analyse(positions, issues, base="USD", manual_rates=None, use_live=True):
    base = (base or "USD").upper()
    alerts = [dict(severity="DATA", **i) for i in issues]

    currencies = {p["currency"] for p in positions}
    rates, rate_src, missing = resolve_rates(currencies, base, manual_rates or {}, use_live)
    for c in missing:
        alerts.append({"severity": "DATA", "symbol": c,
                       "issue": f"No FX rate for {c}->{base} — its positions are excluded from {base} totals. Enter a rate and re-run."})

    # ── per-position values (local + base) ───────────────────────────────────
    for p in positions:
        q, px, cost = p["quantity"], p["market_price"], p["avg_cost"]
        fx = rates.get(p["currency"])
        p["fx_rate"] = fx
        p["market_value"] = _r(q * px) if px else None
        p["cost_basis"]   = _r(q * cost) if cost is not None else None
        p["unrealised_pnl"] = (_r(p["market_value"] - p["cost_basis"])
                               if p["market_value"] is not None and p["cost_basis"] is not None else None)
        p["pnl_pct"] = (_r(p["unrealised_pnl"] / abs(p["cost_basis"]) * 100)
                        if p["unrealised_pnl"] is not None and p["cost_basis"] else None)
        p["market_value_base"]   = _r(p["market_value"] * fx) if fx and p["market_value"] is not None else None
        p["cost_basis_base"]     = _r(p["cost_basis"] * fx) if fx and p["cost_basis"] is not None else None
        p["unrealised_pnl_base"] = _r(p["unrealised_pnl"] * fx) if fx and p["unrealised_pnl"] is not None else None

    # ── roll-ups (all in base currency) ──────────────────────────────────────
    gross = defaultdict(float)           # gross |MV| per account, for weights
    for p in positions:
        if p["market_value_base"] is not None:
            gross[p["account"]] += abs(p["market_value_base"])

    def bucket():
        return {"market_value": 0.0, "gross_exposure": 0.0, "cost_basis": 0.0,
                "unrealised_pnl": 0.0, "positions": 0}

    accounts = defaultdict(lambda: dict(bucket(), long=0.0, short=0.0, currencies=set(),
                                        top_holding=None, top_weight_pct=0.0))
    symbols  = defaultdict(lambda: dict(bucket(), quantity=0.0, accounts=set(),
                                        asset_class=None, currency=None))
    by_class = defaultdict(bucket)
    by_ccy   = defaultdict(lambda: dict(bucket(), market_value_local=0.0))
    acct_count = defaultdict(int)
    for p in positions:
        acct_count[p["account"]] += 1

    for p in positions:
        a, s = accounts[p["account"]], symbols[p["symbol"]]
        c, k = by_class[p["asset_class"]], by_ccy[p["currency"]]
        a["positions"] += 1; s["positions"] += 1; c["positions"] += 1; k["positions"] += 1
        a["currencies"].add(p["currency"])
        s["quantity"] += p["quantity"]; s["accounts"].add(p["account"])
        s["asset_class"], s["currency"] = p["asset_class"], p["currency"]
        if p["market_value"] is not None:
            k["market_value_local"] += p["market_value"]

        mv = p["market_value_base"]
        if mv is None:
            p["weight_pct"] = None
            continue
        for b in (a, s, c, k):
            b["market_value"]   += mv
            b["gross_exposure"] += abs(mv)
            b["cost_basis"]     += p["cost_basis_base"] or 0
            b["unrealised_pnl"] += p["unrealised_pnl_base"] or 0
        if mv >= 0: a["long"] += mv
        else:       a["short"] += mv

        total = gross[p["account"]]
        p["weight_pct"] = _r(abs(mv) / total * 100) if total else None
        if p["weight_pct"] and p["weight_pct"] > a["top_weight_pct"]:
            a["top_weight_pct"], a["top_holding"] = p["weight_pct"], p["symbol"]

        if p["weight_pct"] and p["weight_pct"] > CONCENTRATION_LIMIT_PCT and acct_count[p["account"]] > 1:
            alerts.append({"severity": "RISK", "account": p["account"], "symbol": p["symbol"],
                           "issue": f"Concentration {p['weight_pct']}% of gross exceeds {CONCENTRATION_LIMIT_PCT}% limit"})
        if p["pnl_pct"] is not None and p["pnl_pct"] < LOSS_ALERT_PCT:
            alerts.append({"severity": "RISK", "account": p["account"], "symbol": p["symbol"],
                           "issue": f"Unrealised loss {p['pnl_pct']}% ({base} {p['unrealised_pnl_base']:,.2f})"})
        if p["quantity"] < 0:
            alerts.append({"severity": "INFO", "account": p["account"], "symbol": p["symbol"],
                           "issue": "Short position — confirm borrow / locate"})

    def finish(d, key_name, extra=None):
        rows = []
        for k, v in d.items():
            row = {key_name: k}
            for f, val in v.items():
                if isinstance(val, set):
                    row[f] = sorted(val) if f != "accounts" else len(val)
                elif isinstance(val, float):
                    row[f] = round(val, 6 if f == "quantity" else 2)
                else:
                    row[f] = val
            row["pnl_pct"] = _r(row["unrealised_pnl"] / abs(row["cost_basis"]) * 100) if row.get("cost_basis") else None
            rows.append(row)
        rows.sort(key=lambda r: -abs(r["gross_exposure"]))
        return rows

    acct_rows = finish(accounts, "account")
    gross_total = sum(r["gross_exposure"] for r in acct_rows)
    for r in acct_rows:
        r["share_of_firm_pct"] = _r(r["gross_exposure"] / gross_total * 100) if gross_total else None

    summary = {
        "base_currency": base,
        "positions": len(positions),
        "accounts": len(accounts),
        "symbols": len(symbols),
        "market_value": _r(sum(r["market_value"] for r in acct_rows)),
        "gross_exposure": _r(gross_total),
        "long": _r(sum(r["long"] for r in acct_rows)),
        "short": _r(sum(r["short"] for r in acct_rows)),
        "cost_basis": _r(sum(r["cost_basis"] for r in acct_rows)),
        "unrealised_pnl": _r(sum(r["unrealised_pnl"] for r in acct_rows)),
        "unconverted_positions": sum(1 for p in positions if p["market_value"] is not None and p["market_value_base"] is None),
        "alerts": {s: sum(1 for x in alerts if x["severity"] == s) for s in ("RISK", "DATA", "INFO")},
    }

    positions.sort(key=lambda p: (p["account"], -abs(p["market_value_base"] or 0)))
    return {
        "summary": summary,
        "fx": {"base_currency": base,
               "rates": [{"currency": c, "rate_to_base": _r(rates[c], 6), "source": rate_src[c]} for c in sorted(rates)],
               "missing": missing},
        "accounts": acct_rows,
        "symbols": finish(symbols, "symbol"),
        "exposure": {"asset_class": finish(by_class, "asset_class"),
                     "currency": finish(by_ccy, "currency")},
        "positions": positions,
        "alerts": alerts,
    }


def analyse_text(text, base="USD", manual_rates=None, use_live=True):
    issues = []
    if looks_like_fix(text):
        positions, fills = parse_fix_fills(text, issues)
        source = {"format": "FIX 35=8 fills", "fills_processed": fills}
    else:
        positions = parse_holdings_csv(text, issues)
        source = {"format": "Holdings CSV"}
    if not positions:
        raise ValueError("No positions found in file.")
    result = analyse(positions, issues, base, manual_rates, use_live)
    result["source"] = source
    return result


# ─────────────────────────────────────────────────────────────────────────────
# LAMBDA / REST
#   POST /portfolio  { content: <base64 file>,
#                      base_currency: "USD",            (optional)
#                      fx_rates: {"EUR": 1.08, ...},    (optional, units of base per 1 CCY)
#                      live_rates: true }               (optional, default true)
# ─────────────────────────────────────────────────────────────────────────────
def _resp(code, body):
    return {
        "statusCode": code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    try:
        method = event.get("requestContext", {}).get("http", {}).get("method", "")
        if method == "OPTIONS":
            return _resp(200, {})

        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        data = json.loads(body)

        file_b64 = (data.get("content") or "").strip()
        if not file_b64:
            return _resp(400, {"error": "No file content provided."})
        try:
            text = base64.b64decode(file_b64).decode("utf-8-sig", errors="replace")
        except Exception:
            text = file_b64

        base = (data.get("base_currency") or "USD").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", base):
            return _resp(400, {"error": "base_currency must be a 3-letter ISO code."})
        manual = {str(k).upper(): v for k, v in (data.get("fx_rates") or {}).items()}
        use_live = bool(data.get("live_rates", True))

        return _resp(200, analyse_text(text, base, manual, use_live))
    except ValueError as e:
        return _resp(400, {"error": str(e)})
    except Exception as e:
        return _resp(500, {"error": f"Internal error: {e}"})
