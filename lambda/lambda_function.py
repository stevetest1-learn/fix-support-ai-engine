import json
import os
import re
import crypto_dialects
import xml.etree.ElementTree as ET

# ─────────────────────────────────────────────────────────────────────────────
# Repository field-type string -> internal validation category
# ─────────────────────────────────────────────────────────────────────────────
TYPE_MAP = {
    "INT": "INT", "SEQNUM": "INT", "NUMINGROUP": "INT", "LENGTH": "INT", "DAYOFMONTH": "INT",
    "FLOAT": "FLOAT", "QTY": "FLOAT", "PRICE": "FLOAT", "PRICEOFFSET": "FLOAT",
    "AMT": "FLOAT", "PERCENTAGE": "FLOAT",
    "CHAR": "CHAR",
    "BOOLEAN": "BOOLEAN",
    "UTCTIMESTAMP": "UTCTIMESTAMP", "TZTIMESTAMP": "UTCTIMESTAMP",
    "UTCDATE": "DATE", "LOCALMKTDATE": "DATE",
    "STRING": "STRING", "MULTIPLEVALUESTRING": "STRING", "MULTIPLESTRINGVALUE": "STRING",
    "CURRENCY": "STRING", "EXCHANGE": "STRING", "COUNTRY": "STRING",
    "DATA": "STRING", "XMLDATA": "STRING", "LANGUAGE": "STRING",
    "MONTHYEAR": "STRING", "UTCTIMEONLY": "STRING", "TZTIMEONLY": "STRING",
}

STANDARD_HEADER_REQUIRED = {
    "8": "BeginString", "9": "BodyLength", "35": "MsgType",
    "49": "SenderCompID", "56": "TargetCompID", "34": "MsgSeqNum", "52": "SendingTime",
}
STANDARD_TRAILER_REQUIRED = {"10": "CheckSum"}

ISO_CURRENCY_CODES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNY", "HKD",
    "SGD", "SEK", "NOK", "DKK", "MXN", "ZAR", "TRY", "INR", "BRL", "RUB",
    "KRW", "PLN", "THB", "ILS", "AED", "SAR", "TWD", "IDR", "MYR", "PHP",
    "CZK", "HUF", "CLP", "COP", "ARS",
}

SECURITY_TYPE_ASSET_CLASS = {
    "CS": "Equities (Common Stock)", "PS": "Equities (Preferred Stock)",
    "WAR": "Equities (Warrant)", "OPT": "Options", "FUT": "Futures",
    "CORP": "Fixed Income (Corporate Bond)", "MUNI": "Fixed Income (Municipal Bond)",
    "GOVT": "Fixed Income (Government Security)", "TBILL": "Fixed Income (US Treasury Bill)",
    "TBOND": "Fixed Income (US Treasury Bond)", "TNOTE": "Fixed Income (US Treasury Note)",
    "CD": "Money Market (Certificate of Deposit)", "BA": "Money Market (Bankers Acceptance)",
    "CP": "Money Market (Commercial Paper)", "MBS": "Fixed Income (Mortgage-Backed Security)",
    "CMO": "Fixed Income (Collateralized Mortgage Obligation)", "MF": "Mutual Fund",
    "RP": "Money Market (Repurchase Agreement)",
}


def is_fx_pair(symbol):
    if not symbol:
        return False, None, None
    cleaned = symbol.replace("/", "").replace("-", "").replace(" ", "").upper()
    if len(cleaned) == 6:
        base, quote = cleaned[:3], cleaned[3:]
        if base in ISO_CURRENCY_CODES and quote in ISO_CURRENCY_CODES:
            return True, base, quote
    return False, None, None


def is_equity_ticker(symbol):
    if not symbol:
        return False
    return bool(re.match(r'^[A-Z]{1,5}([.\-][A-Z]{1,2})?$', symbol))


def classify_asset_class(pairs_dict):
    security_type = pairs_dict.get("167")
    symbol = pairs_dict.get("55", "")
    if security_type and security_type in SECURITY_TYPE_ASSET_CLASS:
        return SECURITY_TYPE_ASSET_CLASS[security_type], symbol
    if any(t in pairs_dict for t in ("200", "201", "202", "206")):
        return "Options", symbol
    if any(t in pairs_dict for t in ("223", "224", "225", "228")):
        return "Fixed Income (Bonds)", symbol
    is_fx, base, quote = is_fx_pair(symbol)
    if is_fx:
        return "FX / Currency", f"{base}/{quote}"
    if is_equity_ticker(symbol):
        return "Equities (Stock Symbol)", symbol
    if symbol:
        return "Unclassified / Other", symbol
    return "Unknown (no Symbol or SecurityType present)", ""


def annotate_symbol_tag(symbol):
    is_fx, base, quote = is_fx_pair(symbol)
    if is_fx:
        return f"FX Currency Pair ({base}/{quote})"
    if is_equity_ticker(symbol):
        return "Stock Ticker / Equity Symbol"
    return "Symbol format not recognized as equity ticker or FX pair"


# ─────────────────────────────────────────────────────────────────────────────
# REPOSITORY LOADER - reads from the bundled repository/ folder shipped
# alongside this file in the Lambda deployment package
# ─────────────────────────────────────────────────────────────────────────────

def load_version_repository(version_dir, version_label):
    fields_path = os.path.join(version_dir, "Fields.xml")
    enums_path = os.path.join(version_dir, "Enums.xml")
    msgtype_path = os.path.join(version_dir, "MsgType.xml")
    msgcontents_path = os.path.join(version_dir, "MsgContents.xml")

    if not os.path.exists(fields_path):
        return None

    fields = {}
    try:
        tree = ET.parse(fields_path)
        for rec in tree.getroot().findall("Fields"):
            tag = (rec.findtext("Tag") or "").strip()
            name = (rec.findtext("FieldName") or "").strip()
            ftype = (rec.findtext("Type") or "String").strip()
            desc = (rec.findtext("Desc") or "").strip()
            if tag:
                fields[tag] = [name, ftype, desc, {}]
    except ET.ParseError:
        return None

    if os.path.exists(enums_path):
        try:
            tree = ET.parse(enums_path)
            for rec in tree.getroot().findall("Enums"):
                tag = (rec.findtext("Tag") or "").strip()
                enum_val = rec.findtext("Enum")
                desc = (rec.findtext("Description") or "").strip()
                if tag in fields and enum_val is not None:
                    fields[tag][3][enum_val] = desc
        except ET.ParseError:
            pass

    messages = {}
    msgid_to_msgtype = {}
    if os.path.exists(msgtype_path):
        try:
            tree = ET.parse(msgtype_path)
            for rec in tree.getroot().findall("MsgType"):
                code = (rec.findtext("MsgType") or "").strip()
                name = (rec.findtext("MessageName") or "").strip()
                msgid = (rec.findtext("MsgID") or "").strip()
                if code and msgid:
                    messages[code] = {"name": name, "required": []}
                    msgid_to_msgtype[msgid] = code
        except ET.ParseError:
            pass

    if os.path.exists(msgcontents_path):
        try:
            tree = ET.parse(msgcontents_path)
            for rec in tree.getroot().findall("MsgContents"):
                msgid = (rec.findtext("MsgID") or "").strip()
                tagtext = (rec.findtext("TagText") or "").strip()
                reqd = (rec.findtext("Reqd") or "0").strip()
                if reqd == "1" and tagtext.isdigit() and msgid in msgid_to_msgtype:
                    code = msgid_to_msgtype[msgid]
                    messages[code]["required"].append(tagtext)
        except ET.ParseError:
            pass

    return {
        "version": version_label,
        "fields": {tag: (v[0], v[1], v[3]) for tag, v in fields.items()},
        "field_descriptions": {tag: v[2] for tag, v in fields.items()},
        "header_required": dict(STANDARD_HEADER_REQUIRED),
        "trailer_required": dict(STANDARD_TRAILER_REQUIRED),
        "messages": messages,
    }


def load_fix_dictionaries(root_dir):
    dictionaries = {}
    if not os.path.isdir(root_dir):
        return dictionaries
    for entry in sorted(os.listdir(root_dir)):
        full_path = os.path.join(root_dir, entry)
        if not os.path.isdir(full_path):
            continue
        parsed = load_version_repository(full_path, entry)
        if parsed is not None:
            dictionaries[parsed["version"]] = parsed
    return dictionaries


# Loaded once per cold start, reused across warm Lambda invocations.
REPO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repository")
DICTIONARIES = load_fix_dictionaries(REPO_DIR)


# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE PARSER / VALIDATOR
# ─────────────────────────────────────────────────────────────────────────────

def detect_delimiter(line):
    if "\x01" in line:
        return "\x01"
    if "|" in line:
        return "|"
    if "^" in line:
        return "^"
    return "|"


def parse_fix_message(raw_line, delimiter):
    segments = raw_line.strip().split(delimiter)
    pairs = []
    for seg in segments:
        seg = seg.strip()
        if seg and "=" in seg:
            tag, _, value = seg.partition("=")
            pairs.append((tag.strip(), value.strip()))
    return pairs


def lookup_tag(tag, value, dictionary):
    fields = dictionary.get("fields", {})
    descriptions = dictionary.get("field_descriptions", {})
    if tag in fields:
        field_name, ftype, values = fields[tag]
        value_def = values.get(value, "")
        desc = descriptions.get(tag, "") or f"FIX data type: {ftype}"
        return {"tag": tag, "field_name": field_name, "value": value,
                "value_def": value_def, "description": desc, "known": True}
    return {"tag": tag, "field_name": "UNKNOWN TAG", "value": value, "value_def": "",
            "description": f"Tag not defined in {dictionary.get('version', 'loaded')} dictionary", "known": False}


def get_msg_type_label(pairs_dict, dictionary):
    msg_type_val = pairs_dict.get("35")
    if msg_type_val and msg_type_val in dictionary.get("messages", {}):
        return dictionary["messages"][msg_type_val].get("name", msg_type_val)
    return f"Unknown MsgType ({msg_type_val})" if msg_type_val else "Unknown"


def validate_message_structure(pairs, pairs_dict, dictionary, version_note=None):
    errors = []
    if version_note:
        errors.append(version_note)
    if not pairs:
        errors.append("Unable to parse any tag=value pairs - not a valid FIX message")
        return errors

    fields = dictionary.get("fields", {})
    header_required = dictionary.get("header_required", {})
    trailer_required = dictionary.get("trailer_required", {})
    messages = dictionary.get("messages", {})

    for tag, name in header_required.items():
        if tag not in pairs_dict:
            errors.append(f"Missing required header tag {tag} ({name})")
    for tag, name in trailer_required.items():
        if tag not in pairs_dict:
            errors.append(f"Missing required trailer tag {tag} ({name})")

    msg_type_val = pairs_dict.get("35")
    if msg_type_val and messages and msg_type_val not in messages:
        errors.append(f"Tag 35 (MsgType) = '{msg_type_val}' is not defined in the {dictionary.get('version')} repository")

    for tag, value in pairs:
        if tag not in fields:
            continue
        field_name, ftype, _ = fields[tag]
        dtype = TYPE_MAP.get(ftype.upper(), "STRING")
        if value == "":
            errors.append(f"Tag {tag} ({field_name}) is present but has an empty value")
            continue
        if dtype == "INT" and not re.match(r'^-?\d+$', value):
            errors.append(f"Tag {tag} ({field_name}) expected an integer ({ftype}) but found non-numeric characters: '{value}'")
        elif dtype == "FLOAT" and not re.match(r'^-?\d+(\.\d+)?$', value):
            errors.append(f"Tag {tag} ({field_name}) expected a numeric value ({ftype}) but found invalid characters: '{value}'")
        elif dtype == "BOOLEAN" and value not in ("Y", "N"):
            errors.append(f"Tag {tag} ({field_name}) expected Y or N but found: '{value}'")
        elif dtype == "UTCTIMESTAMP" and not re.match(r'^\d{8}-\d{2}:\d{2}:\d{2}(\.\d+)?$', value):
            errors.append(f"Tag {tag} ({field_name}) expected UTC timestamp format YYYYMMDD-HH:MM:SS but found: '{value}'")
        elif dtype == "DATE" and not re.match(r'^\d{8}$', value):
            errors.append(f"Tag {tag} ({field_name}) expected date format YYYYMMDD but found: '{value}'")
        elif dtype == "CHAR" and len(value) != 1:
            errors.append(f"Tag {tag} ({field_name}) expected a single character but found: '{value}'")

    if msg_type_val and msg_type_val in messages:
        for req_tag in messages[msg_type_val].get("required", []):
            if req_tag not in pairs_dict:
                fname = fields.get(req_tag, (req_tag,))[0]
                msgname = messages[msg_type_val].get("name", msg_type_val)
                errors.append(f"Tag {req_tag} ({fname}) is required for MsgType={msg_type_val} ({msgname}) but is missing")

    return errors


def parse_and_validate(raw_message):
    delimiter = detect_delimiter(raw_message)
    pairs = parse_fix_message(raw_message, delimiter)
    pairs_dict = dict(pairs)
    

    version = pairs_dict.get("8")
    version_note = None
    if version is None:
        version_note = "Missing tag 8 (BeginString) - cannot determine FIX version; tags shown without definitions"
        dictionary = {"fields": {}, "field_descriptions": {}, "header_required": {}, "trailer_required": {}, "messages": {}, "version": "UNKNOWN"}
    elif version not in DICTIONARIES:
        available = ", ".join(sorted(DICTIONARIES.keys()))
        version_note = f"No dictionary loaded for FIX version '{version}' (available: {available}) - tags shown without definitions"
        dictionary = {"fields": {}, "field_descriptions": {}, "header_required": {}, "trailer_required": {}, "messages": {}, "version": version}
    else:
        dictionary = DICTIONARIES[version]
    # Detect and apply crypto venue dialect overlay
        venue_name, venue_signals = crypto_dialects.detect_venue(pairs_dict)
        if venue_name:
            dictionary = crypto_dialects.apply_dialect(dictionary, venue_name)

    results = [lookup_tag(tag, value, dictionary) for tag, value in pairs]
    msg_type_label = get_msg_type_label(pairs_dict, dictionary)
    asset_class, asset_detail = classify_asset_class(pairs_dict)
    

    tags_out = []
    for r in results:
        entry = {
            "tag": r["tag"], "field_name": r["field_name"], "value": r["value"],
            "value_definition": r["value_def"], "description": r["description"], "known": r["known"],
        }
        if r["tag"] == "55":
            entry["symbol_annotation"] = annotate_symbol_tag(r["value"])
        tags_out.append(entry)

    return {
        "raw_message": raw_message,
        "msg_type_label": msg_type_label,
        "fix_version_detected": dictionary.get("version", "UNKNOWN"),
        "available_versions": sorted(DICTIONARIES.keys()),
        "asset_class": asset_class,
        "asset_detail": asset_detail,
        "tags": tags_out,
        "crypto_venue":         venue_name,
        "crypto_venue_signals": venue_signals,
    }


def _response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body_dict),
    }


def lambda_handler(event, context):
    try:
        method = event.get("requestContext", {}).get("http", {}).get("method", "")
        if method == "OPTIONS":
            return _response(200, {})

        if not DICTIONARIES:
            return _response(500, {"error": "No FIX dictionaries are loaded in this deployment - check the bundled repository/ folder."})

        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")

        data = json.loads(body)
        raw_message = (data.get("message") or "").strip()
        if not raw_message:
            return _response(400, {"error": "No FIX message provided. Paste a pipe-delimited FIX message."})

        result = parse_and_validate(raw_message)
        return _response(200, result)
    except Exception as e:
        return _response(500, {"error": f"Unexpected error: {str(e)}"})