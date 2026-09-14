"""
FIX Support AI Engine — STP Generator Lambda

Takes a FIX 35=8 Execution Report fill (ExecType 150=F, OrdStatus 39=1 or 39=2)
and generates the corresponding 35=AE Trade Capture Report for STP submission
to clearing / settlement. Returns:
  - decoded 35=8 tags
  - the generated 35=AE message (pipe-delimited)
  - decoded 35=AE tags
  - field mapping table showing how every field was derived
  - validation errors and settlement break risk warnings

No external dependencies — pure Python stdlib.
"""

import json
import re
import base64
import uuid
from datetime import datetime, timedelta, timezone


# ─────────────────────────────────────────────────────────────────────────────
# FIX TAG REFERENCE (focused on 35=8 and 35=AE fields)
# ─────────────────────────────────────────────────────────────────────────────
FIX_TAGS = {
    "1":  ("Account",           "Account mnemonic — critical for settlement", {}),
    "6":  ("AvgPx",             "Average fill price", {}),
    "8":  ("BeginString",       "FIX version identifier", {}),
    "9":  ("BodyLength",        "Message body length in bytes", {}),
    "10": ("CheckSum",          "Three-digit message checksum", {}),
    "11": ("ClOrdID",           "Client-assigned order identifier", {}),
    "12": ("Commission",        "Commission amount", {}),
    "14": ("CumQty",            "Cumulative quantity filled to date", {}),
    "15": ("Currency",          "Trade currency (ISO 4217) — required for settlement", {}),
    "17": ("ExecID",            "Execution ID assigned by the executing broker", {}),
    "30": ("LastMkt",           "Market / venue where this fill occurred", {}),
    "31": ("LastPx",            "Fill price for this execution", {}),
    "32": ("LastQty",           "Fill quantity for this execution", {}),
    "34": ("MsgSeqNum",         "Message sequence number", {}),
    "35": ("MsgType",           "FIX message type identifier", {
        "8": "Execution Report",
        "AE": "Trade Capture Report (STP event to clearing/settlement)",
    }),
    "37": ("OrderID",           "Broker-assigned order identifier", {}),
    "38": ("OrderQty",          "Original order quantity", {}),
    "39": ("OrdStatus",         "Current order status", {
        "0": "New", "1": "Partially Filled", "2": "Filled",
        "3": "Done for Day", "4": "Canceled", "5": "Replaced",
        "6": "Pending Cancel", "8": "Rejected", "A": "Pending New",
        "C": "Expired", "E": "Pending Replace",
    }),
    "40": ("OrdType",           "Order type", {
        "1": "Market", "2": "Limit", "3": "Stop", "4": "Stop Limit",
        "P": "Pegged",
    }),
    "44": ("Price",             "Limit price of the order", {}),
    "49": ("SenderCompID",      "Sender company identifier", {}),
    "50": ("SenderSubID",       "Sender sub-identifier (desk / trader)", {}),
    "52": ("SendingTime",       "Time message was sent (UTC)", {}),
    "54": ("Side",              "Order side — critical for settlement", {
        "1": "Buy", "2": "Sell", "3": "Buy Minus", "4": "Sell Plus",
        "5": "Sell Short", "6": "Sell Short Exempt",
    }),
    "55": ("Symbol",            "Security ticker symbol", {}),
    "56": ("TargetCompID",      "Target company identifier", {}),
    "57": ("TargetSubID",       "Target sub-identifier", {}),
    "58": ("Text",              "Free format text / order notes", {}),
    "59": ("TimeInForce",       "Order time in force", {
        "0": "Day", "1": "Good Till Cancel (GTC)",
        "3": "Immediate or Cancel (IOC)", "4": "Fill or Kill (FOK)", "6": "Good Till Date",
    }),
    "60": ("TransactTime",      "Trade execution time (UTC)", {}),
    "63": ("SettlType",         "Settlement type / settlement period", {
        "0": "Regular (T+2 equities / T+1 FX)", "1": "Cash (same day)",
        "2": "Next Day (T+1)", "3": "T+2", "4": "T+3", "5": "T+4",
        "6": "Future", "7": "When Issued", "8": "Sellers Option",
    }),
    "64": ("FutSettDate",       "Settlement date (YYYYMMDD) — critical for settlement", {}),
    "75": ("TradeDate",         "Date the trade was executed (YYYYMMDD)", {}),
    "100": ("ExDestination",    "Execution destination / exchange", {}),
    "120": ("SettlCurrency",    "Currency for settlement — may differ from trade currency", {}),
    "150": ("ExecType",         "Specific execution report type", {
        "0": "New", "1": "Partial Fill", "2": "Fill",
        "3": "Done for Day", "4": "Canceled", "5": "Replace",
        "6": "Pending Cancel", "7": "Stopped", "8": "Rejected",
        "9": "Suspended", "A": "Pending New", "B": "Calculated",
        "C": "Expired", "D": "Restated", "E": "Pending Replace",
        "F": "Trade — Partial or Full Fill (STP trigger)",
        "G": "Trade Correct", "H": "Trade Cancel",
    }),
    "151": ("LeavesQty",        "Quantity remaining open for further execution", {}),
    "167": ("SecurityType",     "Security type", {
        "CS": "Common Stock", "PS": "Preferred Stock",
        "CORP": "Corporate Bond", "GOVT": "Government Security",
        "MUNI": "Municipal Bond", "OPT": "Option", "FUT": "Future",
        "FX": "FX Spot", "MF": "Mutual Fund",
    }),
    "198": ("SecondaryOrderID", "Secondary order ID assigned by the executing venue", {}),
    "207": ("SecurityExchange", "Exchange / market where the security is listed", {}),
    "381": ("GrossTradeAmt",    "Gross trade amount = LastQty × LastPx (used for settlement)", {}),
    "447": ("PartyIDSource",    "Source / type of the PartyID value", {
        "B": "BIC (Bank Identifier Code)", "C": "Generally accepted market participant ID",
        "D": "Proprietary / Custom code", "E": "ISO Country Code",
        "F": "Settlement Entity Location", "G": "MIC (Market Identifier Code)",
        "H": "CSD Participant / Member Code", "I": "CSD",
        "J": "Taxpayer Identifier Number",
    }),
    "448": ("PartyID",          "Identifier for this party in the repeating group", {}),
    "452": ("PartyRole",        "Role of this party in the transaction", {
        "1": "Executing Firm", "2": "Broker of Credit", "3": "Client ID",
        "4": "Clearing Firm", "5": "Investor ID", "6": "Introducing Firm",
        "7": "Entering Firm", "11": "Order Origination Firm",
        "12": "Executing Trader", "13": "Order Origination Trader",
        "17": "Contra Firm", "21": "Contra Investor ID",
        "22": "Transfer To Firm", "24": "Customer Account",
        "25": "Investment Decision Maker", "26": "Reporting Market Maker",
        "38": "Clearing Organization",
    }),
    "453": ("NoPartyIDs",       "Number of party entries in the NoPartyIDs repeating group", {}),
    "487": ("TradeReportTransType", "Trade capture report transaction type", {
        "0": "New", "1": "Cancel", "2": "Replace",
        "3": "Release", "4": "Reverse", "5": "Cancel Due To Back Out",
    }),
    "526": ("SecondaryClOrdID", "Secondary client order ID", {}),
    "570": ("PreviouslyReported","Indicates whether this trade was previously reported", {
        "Y": "Trade was previously reported to the counterparty",
        "N": "First time this trade is being reported",
    }),
    "571": ("TradeReportID",    "Unique identifier for this Trade Capture Report", {}),
    "828": ("TrdType",          "Broad category of the trade", {
        "0": "Regular Trade", "1": "Block Trade", "2": "Exchange for Physical (EFP)",
        "3": "Transfer", "4": "Late Trade", "5": "T Trade",
        "6": "Weighted Average Price Trade", "7": "Bunched Trade",
        "10": "After Hours Trade", "22": "Privately Negotiated Trade",
    }),
    "856": ("TradeReportType",  "Specific type / status of this trade report", {
        "0": "Submitted (trade report submitted for matching)",
        "1": "Accepted (trade report matched and accepted)",
        "2": "Adjudicated", "3": "Advisor", "5": "Counter",
        "6": "Declined", "10": "Rejected",
    }),
    "880": ("TrdMatchID",       "Unique trade match ID assigned by the matching / clearing system", {}),
    "1003": ("TradeID",         "Unique trade identifier assigned at the trading venue", {}),
}

# Tags whose absence in the 35=8 signal a settlement break risk
BREAK_RISK_TAGS = {"1", "15", "63", "64", "55", "54", "32", "31", "60"}

# Required tags for a valid 35=8 fill
REQUIRED_EXEC_TAGS = ["35", "49", "56", "17", "55", "54", "31", "32", "60", "150", "39"]


# ─────────────────────────────────────────────────────────────────────────────
# PARSING & UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def detect_delimiter(line):
    if "\x01" in line: return "\x01"
    if "|"    in line: return "|"
    if "^"    in line: return "^"
    return "|"


def parse_fix(message):
    """Return (pairs list, dict, delimiter)."""
    delim = detect_delimiter(message)
    pairs, d = [], {}
    for seg in message.split(delim):
        seg = seg.strip()
        if "=" in seg:
            tag, _, val = seg.partition("=")
            tag, val = tag.strip(), val.strip()
            if tag:
                pairs.append((tag, val))
                d[tag] = val
    return pairs, d, delim


def lookup_tag(tag, value):
    if tag in FIX_TAGS:
        name, desc, values = FIX_TAGS[tag]
        return {"tag": tag, "name": name, "description": desc,
                "value": value, "value_meaning": values.get(value, ""), "known": True}
    return {"tag": tag, "name": f"Tag {tag}",
            "description": "Tag not in reference dictionary",
            "value": value, "value_meaning": "", "known": False}


def decode_tags(pairs):
    return [lookup_tag(t, v) for t, v in pairs]


def calculate_checksum(msg_str):
    return str(sum(ord(c) for c in msg_str) % 256).zfill(3)


def calc_settl_date(trade_date_str, days=2):
    """T+N settlement date. Simple calendar-day approximation."""
    try:
        td = datetime.strptime(trade_date_str, "%Y%m%d")
        return (td + timedelta(days=days)).strftime("%Y%m%d")
    except Exception:
        return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y%m%d")


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def validate_exec_report(d):
    """
    Validate the input message is a 35=8 fill (150=F, 39=1 or 39=2).
    Returns (is_valid, errors, warnings).
    """
    errors   = []
    warnings = []

    if d.get("35") != "8":
        errors.append(
            f"Tag 35 (MsgType) must be '8' (Execution Report) — "
            f"found: '{d.get('35', 'MISSING')}'"
        )

    exec_type = d.get("150", "")
    if exec_type != "F":
        errors.append(
            f"Tag 150 (ExecType) must be 'F' (Trade / Fill) — "
            f"found: '{exec_type}'. This tool only processes fill events (150=F). "
            f"ExecType 'F' is the STP trigger for 35=AE generation."
        )

    ord_status = d.get("39", "")
    if ord_status not in ("1", "2"):
        errors.append(
            f"Tag 39 (OrdStatus) must be '1' (Partially Filled) or '2' (Filled) — "
            f"found: '{ord_status}'. "
            f"39=1 (Partial Fill) and 39=2 (Full Fill) are the only statuses that "
            f"trigger STP and require a 35=AE Trade Capture Report."
        )

    # Warn on missing fields
    for tag in REQUIRED_EXEC_TAGS:
        if not d.get(tag):
            name = FIX_TAGS.get(tag, (f"Tag {tag}",))[0]
            warnings.append(
                f"Tag {tag} ({name}) is missing — the generated 35=AE may be incomplete."
            )

    if not d.get("1"):
        warnings.append(
            "⚠ BREAK RISK: Tag 1 (Account) is missing. "
            "Account is required for settlement allocation. "
            "Missing Account is the most common cause of trade settlement breaks."
        )
    if not d.get("15"):
        warnings.append(
            "⚠ BREAK RISK: Tag 15 (Currency) is missing. "
            "Settlement currency must be specified in the 35=AE."
        )
    if not d.get("37"):
        warnings.append(
            "⚠ BREAK RISK: Tag 37 (OrderID) is missing. "
            "The broker OrderID is required to link the fill to the original order in settlement."
        )

    return len(errors) == 0, errors, warnings


# ─────────────────────────────────────────────────────────────────────────────
# 35=AE GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_ae(exec_dict, version="FIX.4.2"):
    """
    Build an ordered list of (tag, value) tuples for the 35=AE message.
    Also returns a metadata dict describing how each field was derived.
    """
    now     = datetime.now(timezone.utc)
    now_str = now.strftime("%Y%m%d-%H:%M:%S.%f")[:-3]

    # TradeDate — extract date portion from TransactTime (tag 60)
    transact_time = exec_dict.get("60", now_str)
    if len(transact_time) >= 8:
        raw_date = transact_time[:8].replace("-", "")
        try:
            datetime.strptime(raw_date, "%Y%m%d")
            trade_date = raw_date
        except ValueError:
            trade_date = now.strftime("%Y%m%d")
    else:
        trade_date = now.strftime("%Y%m%d")

    settl_date = calc_settl_date(trade_date, days=2)

    # GrossTradeAmt
    gross_str = ""
    try:
        lq = float(exec_dict.get("32", 0) or 0)
        lp = float(exec_dict.get("31", 0) or 0)
        if lq and lp:
            gross_str = f"{lq * lp:.2f}"
    except (ValueError, TypeError):
        pass

    # Unique identifiers
    exec_id        = exec_dict.get("17", "")
    trade_report_id = (
        f"TCR-{exec_id}" if exec_id
        else f"TCR-{uuid.uuid4().hex[:12].upper()}"
    )
    cl_ord_id = exec_dict.get("11", "")
    trd_match_id = (
        f"{cl_ord_id}-{exec_id}" if (cl_ord_id and exec_id)
        else uuid.uuid4().hex[:16].upper()
    )

    # In STP: sender = TargetCompID from 35=8 (the firm that received the fill)
    #         target = the clearing/settlement counterparty
    sender = exec_dict.get("56", "CLIENT_FIRM")
    target = exec_dict.get("49", "EXEC_BROKER")

    # ── Build ordered tag list ────────────────────────────────────────────────
    ae = []

    # Session header
    ae.append(("35", "AE"))
    ae.append(("34", "1"))
    ae.append(("49", sender))
    ae.append(("52", now_str))
    ae.append(("56", target))

    # Trade report identity
    ae.append(("571", trade_report_id))  # TradeReportID
    ae.append(("487", "0"))              # TradeReportTransType = New
    ae.append(("570", "N"))              # PreviouslyReported = No
    ae.append(("856", "0"))              # TradeReportType = Submitted
    ae.append(("828", "0"))              # TrdType = Regular Trade
    ae.append(("880", trd_match_id))     # TrdMatchID

    # Order references
    ae.append(("17", exec_id))
    ae.append(("37", exec_dict.get("37", "")))
    ae.append(("11", cl_ord_id))
    if exec_dict.get("198"):
        ae.append(("198", exec_dict["198"]))

    # Instrument
    ae.append(("55", exec_dict.get("55", "")))
    if exec_dict.get("167"): ae.append(("167", exec_dict["167"]))
    if exec_dict.get("207"): ae.append(("207", exec_dict["207"]))

    # Trade details
    ae.append(("54",  exec_dict.get("54", "")))
    ae.append(("32",  exec_dict.get("32", "")))
    ae.append(("31",  exec_dict.get("31", "")))
    if gross_str:
        ae.append(("381", gross_str))
    if exec_dict.get("15"):  ae.append(("15",  exec_dict["15"]))
    if exec_dict.get("120"): ae.append(("120", exec_dict["120"]))

    # Dates and settlement
    ae.append(("60", transact_time))
    ae.append(("75", trade_date))
    ae.append(("63", "0"))           # SettlType = Regular
    ae.append(("64", settl_date))

    # Account
    if exec_dict.get("1"):
        ae.append(("1", exec_dict["1"]))

    # NoPartyIDs repeating group (2 parties: executing firm + client)
    account    = exec_dict.get("1") or exec_dict.get("56", "CLIENT")
    exec_firm  = exec_dict.get("49", "EXEC_FIRM")
    ae.append(("453", "2"))           # 2 parties
    ae.append(("448", exec_firm))     # Party 1: Executing Firm
    ae.append(("447", "D"))
    ae.append(("452", "1"))           # Role: Executing Firm
    ae.append(("448", account))       # Party 2: Client / Customer Account
    ae.append(("447", "D"))
    ae.append(("452", "24"))          # Role: Customer Account

    # Optional passthrough fields
    for opt in ["58", "30", "100", "526", "6"]:
        if exec_dict.get(opt):
            ae.append((opt, exec_dict[opt]))

    return ae


def build_fix_string(ordered_tags, version="FIX.4.2"):
    """
    Serialise ordered (tag, value) list to a complete FIX pipe-delimited string
    with correct BodyLength (tag 9) and CheckSum (tag 10).
    """
    # Body: everything except 8, 9, 10
    body_parts = [f"{t}={v}" for t, v in ordered_tags if t not in ("8", "9", "10") and v != ""]
    body_str   = "|".join(body_parts) + "|"
    body_len   = len(body_str)

    preamble    = f"8={version}|9={body_len}|"
    checksum    = calculate_checksum(preamble + body_str)

    return f"{preamble}{body_str}10={checksum}|"


# ─────────────────────────────────────────────────────────────────────────────
# FIELD MAPPING TABLE
# ─────────────────────────────────────────────────────────────────────────────

def build_field_mapping(exec_dict, ae_dict):
    """
    Build a derivation table showing how each 35=AE field was produced from 35=8.
    Each entry: source tag/value → target tag/value + rule + break_risk flag.
    """
    RULES = [
        # src_tag, src_label,    tgt_tag, tgt_label,      derivation,                         critical
        ("17", "ExecID",         "17",  "ExecID",          "Directly mapped from execution report",                  False),
        ("37", "OrderID",        "37",  "OrderID",         "Directly mapped from execution report",                  True),
        ("11", "ClOrdID",        "11",  "ClOrdID",         "Directly mapped from execution report",                  True),
        ("55", "Symbol",         "55",  "Symbol",          "Directly mapped — must match settlement record",         True),
        ("54", "Side",           "54",  "Side",            "Directly mapped — critical for DVP settlement direction",True),
        ("32", "LastQty",        "32",  "LastQty",         "Fill quantity mapped directly — must match clearing",     True),
        ("31", "LastPx",         "31",  "LastPx",          "Fill price mapped directly — must match clearing",       True),
        ("60", "TransactTime",   "60",  "TransactTime",    "Directly mapped — execution timestamp",                  False),
        ("1",  "Account",        "1",   "Account",         "Directly mapped — BREAK RISK if missing",                True),
        ("15", "Currency",       "15",  "Currency",        "Directly mapped — required for settlement CCY matching",  True),
        ("56", "TargetCompID",   "49",  "SenderCompID",    "Sender/Target SWAPPED — firm that received fill now sends AE",  False),
        ("49", "SenderCompID",   "56",  "TargetCompID",    "Sender/Target SWAPPED — executing broker becomes target",False),
        ("49", "SenderCompID",   "448", "PartyID (Exec)",  "Executing firm added to NoPartyIDs as Role 1 (Executing Firm)", False),
        ("1",  "Account",        "448", "PartyID (Client)","Account added to NoPartyIDs as Role 24 (Customer Account)",     True),
        ("60", "TransactTime",   "75",  "TradeDate",       "Date portion (YYYYMMDD) extracted from TransactTime",    False),
        ("75", "TradeDate",      "64",  "FutSettDate",     "AUTO-CALCULATED: TradeDate + 2 calendar days (T+2 equities)",   True),
        ("—",  "—",              "63",  "SettlType",       "AUTO-SET to '0' (Regular) — update for cash/FX settlement",     False),
        ("31 × 32", "LastPx × LastQty","381","GrossTradeAmt","CALCULATED: LastQty × LastPx — used for settlement valuation",True),
        ("17", "ExecID",         "571", "TradeReportID",   "AUTO-GENERATED: 'TCR-' prefix + ExecID",                False),
        ("11+17","ClOrdID+ExecID","880","TrdMatchID",      "AUTO-GENERATED: ClOrdID + ExecID concatenation",         False),
        ("—",  "—",              "487", "TradeReportTransType","AUTO-SET to '0' (New) — first submission",           False),
        ("—",  "—",              "570", "PreviouslyReported",  "AUTO-SET to 'N' — first time reporting this trade",  False),
        ("—",  "—",              "856", "TradeReportType",  "AUTO-SET to '0' (Submitted)",                           False),
        ("—",  "—",              "828", "TrdType",          "AUTO-SET to '0' (Regular Trade)",                       False),
    ]

    mapping = []
    for src_tag, src_label, tgt_tag, tgt_label, rule, critical in RULES:
        # Get actual source value
        if "×" in src_tag or "+" in src_tag or src_tag == "—":
            src_val = ""
        else:
            src_val = exec_dict.get(src_tag, "")

        tgt_val = ae_dict.get(tgt_tag, "")

        # Determine break risk: critical field that is empty in source
        is_break_risk = critical and not src_val and src_tag not in ("—", "31 × 32", "11+17", "75")

        mapping.append({
            "src_tag":      src_tag,
            "src_label":    src_label,
            "src_value":    src_val,
            "tgt_tag":      tgt_tag,
            "tgt_label":    tgt_label,
            "tgt_value":    tgt_val,
            "rule":         rule,
            "break_risk":   is_break_risk,
            "critical":     critical,
        })

    return mapping


# ─────────────────────────────────────────────────────────────────────────────
# LAMBDA HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def _resp(code, body):
    return {
        "statusCode": code,
        "headers": {
            "Content-Type":                "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers":"Content-Type",
            "Access-Control-Allow-Methods":"POST,OPTIONS",
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

        data    = json.loads(body)
        raw_msg = (data.get("message") or "").strip()
        version = data.get("fix_version", "FIX.4.2")

        if not raw_msg:
            return _resp(400, {"error": "No FIX message provided."})

        # Parse input 35=8
        exec_pairs, exec_dict, _ = parse_fix(raw_msg)

        # Validate
        is_valid, errors, warnings = validate_exec_report(exec_dict)
        if not is_valid:
            return _resp(400, {
                "error":    "Input message failed validation — cannot generate 35=AE.",
                "errors":   errors,
                "warnings": warnings,
                "exec_tags": decode_tags(exec_pairs),
            })

        # Generate 35=AE
        ae_ordered  = generate_ae(exec_dict, version)
        ae_string   = build_fix_string(ae_ordered, version)
        ae_pairs, ae_dict, _ = parse_fix(ae_string)

        # Field mapping
        mapping = build_field_mapping(exec_dict, ae_dict)

        # Break risk summary
        break_risks = [m for m in mapping if m["break_risk"]]

        print(f"STP Generated: {exec_dict.get('17','?')} → {ae_dict.get('571','?')}")

        return _resp(200, {
            "is_valid":       True,
            "errors":         errors,
            "warnings":       warnings,
            "break_risks":    len(break_risks),
            "exec_tags":      decode_tags(exec_pairs),
            "ae_message":     ae_string,
            "ae_tags":        decode_tags(ae_pairs),
            "field_mapping":  mapping,
            "trade_report_id": ae_dict.get("571", ""),
            "trd_match_id":    ae_dict.get("880", ""),
            "settl_date":      ae_dict.get("64", ""),
            "gross_trade_amt": ae_dict.get("381", ""),
        })

    except Exception as exc:
        import traceback
        print(traceback.format_exc())
        return _resp(500, {"error": f"Unexpected error: {str(exc)}"})