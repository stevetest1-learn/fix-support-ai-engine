#!/usr/bin/env python3
"""
FIX Protocol Parser - FIX Trading Community Repository Edition

Loads tag/message definitions from a local FIX Repository export
(one folder per version, each containing Fields.xml, Enums.xml,
MsgType.xml, MsgContents.xml - the classic 2004 Access-style export).
Selects the correct version's dictionary per message based on tag 8
(BeginString), which matches the version folder name directly
(e.g. 'FIX.4.2').

Usage:
    python fix_parser_repo.py <repo_dir> <input_file> <output_file>

Example:
    python fix_parser_repo.py /Users/steve/fix-parser-web/repository fix_messages.txt parsed_output.txt
"""

import sys
import os
import re
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime

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

# Standard session-level header/trailer fields, consistent across versions.
# (This repository format defines StandardHeader/StandardTrailer as named
# components elsewhere rather than a flat field list, so this is a scoped
# baseline rather than something parsed live from Components.xml.)
STANDARD_HEADER_REQUIRED = {
    "8": "BeginString", "9": "BodyLength", "35": "MsgType",
    "49": "SenderCompID", "56": "TargetCompID", "34": "MsgSeqNum", "52": "SendingTime",
}
STANDARD_TRAILER_REQUIRED = {"10": "CheckSum"}

# ─────────────────────────────────────────────────────────────────────────────
# ASSET CLASS DETECTION (version-independent heuristics, unchanged)
# ─────────────────────────────────────────────────────────────────────────────
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
# REPOSITORY LOADER (Fields.xml / Enums.xml / MsgType.xml / MsgContents.xml)
# ─────────────────────────────────────────────────────────────────────────────

def load_version_repository(version_dir, version_label):
    """Load one version folder's Fields/Enums/MsgType/MsgContents XML files."""
    fields_path = os.path.join(version_dir, "Fields.xml")
    enums_path = os.path.join(version_dir, "Enums.xml")
    msgtype_path = os.path.join(version_dir, "MsgType.xml")
    msgcontents_path = os.path.join(version_dir, "MsgContents.xml")

    if not os.path.exists(fields_path):
        return None

    fields = {}  # tag -> [field_name, ftype, desc, {enum_value: description}]
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

    messages = {}          # msgtype_code -> {"name": .., "required": [tags]}
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
                # Only flatten plain numeric tag references that are directly
                # required on the message. Component/StandardHeader/
                # StandardTrailer references (non-numeric TagText) are not
                # expanded here - same scoped simplification as before.
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
        "source_dir": version_dir,
        "schema": "FIX Repository (Fields/Enums/MsgType/MsgContents)",
    }


def load_fix_dictionaries(root_dir):
    """
    Scan root_dir for version subfolders (e.g. 'FIX.4.2', 'FIX.5.0SP1'),
    each containing a Fields.xml at minimum. Returns (dictionaries, skipped).
    """
    dictionaries = {}
    skipped = []

    for entry in sorted(os.listdir(root_dir)):
        full_path = os.path.join(root_dir, entry)
        if not os.path.isdir(full_path):
            continue
        parsed = load_version_repository(full_path, entry)
        if parsed is None:
            skipped.append((full_path, "No usable Fields.xml found in this folder - skipped"))
            continue
        dictionaries[parsed["version"]] = parsed

    return dictionaries, skipped


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
    return "\x01"


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
        return {"tag": tag, "field_name": field_name, "value": value, "value_def": value_def,
                "description": desc, "known": True}
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
        errors.append("Unable to parse any tag=value pairs from this line - not a valid FIX message")
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


def build_message_block(msg_num, raw_line, results, asset_class, asset_detail, errors, dict_version):
    lines = []
    lines.append("=" * 70)
    lines.append(f"  MESSAGE #{msg_num}")
    lines.append("=" * 70)
    lines.append(f"  RAW          : {raw_line.strip()}")
    lines.append(f"  FIX Version  : {dict_version}")
    lines.append(f"  Asset Class  : {asset_class}" + (f"  ({asset_detail})" if asset_detail else ""))
    lines.append(f"  Validation   : {'FAILED (' + str(len(errors)) + ' issue(s))' if errors else 'PASSED'}")
    lines.append("-" * 70)

    if errors:
        lines.append("  VALIDATION ERRORS / NOTES:")
        for e in errors:
            lines.append(f"    - {e}")
        lines.append("-" * 70)

    for r in results:
        tag, field_name, value, value_def, desc = r["tag"], r["field_name"], r["value"], r["value_def"], r["description"]
        if value_def:
            lines.append(f"  Tag {tag:>4} | {field_name:<25} = {value}")
            lines.append(f"           {'':25}   -> {value_def}")
        else:
            lines.append(f"  Tag {tag:>4} | {field_name:<25} = {value}")
        if r["known"] and desc:
            lines.append(f"           {'':25}   ({desc[:90]}{'...' if len(desc) > 90 else ''})")
        if tag == "55":
            lines.append(f"           {'':25}   -> {annotate_symbol_tag(value)}")
        if not r["known"]:
            lines.append(f"           WARNING: {desc}")

    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="FIX Protocol Parser - loads tag/message definitions from a FIX Repository export."
    )
    parser.add_argument("repo_dir", help="Path to the repository directory (contains FIX.x.x version subfolders)")
    parser.add_argument("input_file", help="Path to the file containing FIX messages to parse")
    parser.add_argument("output_file", help="Path to write the parsed output")
    args = parser.parse_args()

    if not os.path.isdir(args.repo_dir):
        print(f"Error: repository directory '{args.repo_dir}' not found.")
        sys.exit(1)
    if not os.path.exists(args.input_file):
        print(f"Error: input file '{args.input_file}' not found.")
        sys.exit(1)

    print(f"\n{'=' * 70}")
    print(f"  FIX Protocol Parser - Repository Edition")
    print(f"  Repository : {args.repo_dir}")
    print(f"  Input      : {args.input_file}")
    print(f"  Output     : {args.output_file}")
    print(f"{'=' * 70}\n")

    print("Scanning repository for version folders...")
    dictionaries, skipped = load_fix_dictionaries(args.repo_dir)

    if not dictionaries:
        print(f"\nERROR: No usable FIX version folders found in '{args.repo_dir}'.")
        print("Expected subfolders (e.g. 'FIX.4.2') each containing at least a Fields.xml.")
        if skipped:
            print(f"\n{len(skipped)} folder(s) were found but rejected:")
            for path, reason in skipped:
                print(f"  - {path}: {reason}")
        sys.exit(1)

    print(f"\nLoaded {len(dictionaries)} FIX dictionary version(s):")
    for v, d in sorted(dictionaries.items()):
        print(f"  - {v:<14} {len(d['fields']):>4} fields, {len(d['messages']):>3} message types")

    if skipped:
        print(f"\nSkipped {len(skipped)} folder(s):")
        for path, reason in skipped:
            print(f"  - {path}: {reason}")
    print()

    msg_count, skip_count, error_msg_count = 0, 0, 0
    asset_class_counts = {}
    version_counts = {}
    output_lines = [
        f"FIX Protocol Parser - Repository Edition - Output\n"
        f"Repository : {args.repo_dir}\n"
        f"Input File : {args.input_file}\n"
        f"Generated  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"{'=' * 70}\n\n"
    ]

    with open(args.input_file, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    for raw_line in raw_lines:
        raw_line = raw_line.strip()
        if not raw_line or raw_line.startswith("#"):
            skip_count += 1
            continue

        msg_count += 1
        delimiter = detect_delimiter(raw_line)
        pairs = parse_fix_message(raw_line, delimiter)
        pairs_dict = dict(pairs)

        version = pairs_dict.get("8")
        version_note = None
        if version is None:
            version_note = "Missing tag 8 (BeginString) - cannot determine FIX version; tags shown without definitions"
            dictionary = {"fields": {}, "field_descriptions": {}, "header_required": {}, "trailer_required": {}, "messages": {}, "version": "UNKNOWN"}
        elif version not in dictionaries:
            available = ", ".join(sorted(dictionaries.keys()))
            version_note = f"No dictionary loaded for FIX version '{version}' (available: {available}) - tags shown without definitions"
            dictionary = {"fields": {}, "field_descriptions": {}, "header_required": {}, "trailer_required": {}, "messages": {}, "version": version}
        else:
            dictionary = dictionaries[version]

        results = [lookup_tag(tag, value, dictionary) for tag, value in pairs]
        label = get_msg_type_label(pairs_dict, dictionary)
        asset_class, asset_detail = classify_asset_class(pairs_dict)
        asset_class_counts[asset_class] = asset_class_counts.get(asset_class, 0) + 1
        version_counts[dictionary.get("version", "UNKNOWN")] = version_counts.get(dictionary.get("version", "UNKNOWN"), 0) + 1
        errors = validate_message_structure(pairs, pairs_dict, dictionary, version_note)
        if errors:
            error_msg_count += 1

        print(f"{'-' * 70}")
        print(f"  MESSAGE #{msg_count}  ->  {label}   [{dictionary.get('version')}]")
        print(f"  Asset Class: {asset_class}" + (f"  ({asset_detail})" if asset_detail else ""))
        print(f"  Validation : {'FAILED (' + str(len(errors)) + ' issue(s))' if errors else 'PASSED'}")
        print(f"{'-' * 70}")

        if errors:
            print("  VALIDATION ERRORS / NOTES:")
            for e in errors:
                print(f"    - {e}")
            print("-" * 70)

        for r in results:
            tag, field_name, value, value_def = r["tag"], r["field_name"], r["value"], r["value_def"]
            if value_def:
                print(f"  Tag {tag:>4} | {field_name:<25} = {value}")
                print(f"           {'':25}   -> {value_def}")
            else:
                print(f"  Tag {tag:>4} | {field_name:<25} = {value}")
            if tag == "55":
                print(f"           {'':25}   -> {annotate_symbol_tag(value)}")
            if not r["known"]:
                print(f"           WARNING: {r['description']}")
        print()

        output_lines.append(build_message_block(msg_count, raw_line, results, asset_class, asset_detail, errors, dictionary.get("version")))

    summary_lines = [
        f"{'=' * 70}", f"  SUMMARY", f"{'=' * 70}",
        f"  Total messages parsed   : {msg_count}",
        f"  Lines skipped           : {skip_count}",
        f"  Messages with errors    : {error_msg_count} of {msg_count}",
        f"  FIX version breakdown   :",
    ]
    for v, c in sorted(version_counts.items(), key=lambda x: -x[1]):
        summary_lines.append(f"      {v:<20} : {c}")
    summary_lines.append(f"  Asset class breakdown   :")
    for ac, c in sorted(asset_class_counts.items(), key=lambda x: -x[1]):
        summary_lines.append(f"      {ac:<35} : {c}")
    summary_lines.append(f"  Completed                : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    summary_lines.append(f"{'=' * 70}\n")
    summary = "\n".join(summary_lines)

    print(summary)
    output_lines.append(summary)

    with open(args.output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"  Parsed output written to '{args.output_file}'\n")


if __name__ == "__main__":
    main()