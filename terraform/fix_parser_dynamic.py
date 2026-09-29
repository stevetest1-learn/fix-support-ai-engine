#!/usr/bin/env python3
"""
FIX Protocol Parser - Dynamic Dictionary Edition

Instead of a hardcoded tag dictionary, this version scans a local
repository directory for FIX data dictionary XML files (QuickFIX-style
or FIX Orchestra), builds an in-memory dictionary per FIX version, and
selects the correct dictionary for each message based on its own
tag 8 (BeginString). Falls back gracefully when a version isn't found.

Usage:
    python fix_parser_dynamic.py <dict_dir> <input_file> <output_file>

Example:
    python fix_parser_dynamic.py /Users/steve/fix-parser-web/repository fix_messages.txt parsed_output.txt
"""

import sys
import os
import re
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# QuickFIX data-type -> internal validation category
# ─────────────────────────────────────────────────────────────────────────────
QUICKFIX_TYPE_MAP = {
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

# ─────────────────────────────────────────────────────────────────────────────
# ASSET CLASS DETECTION (version-independent heuristics, unchanged from before)
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
# DICTIONARY REPOSITORY LOADER
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_version_string(major, minor, servicepack=None):
    v = f"FIX.{major}.{minor}"
    if servicepack and servicepack != "0":
        v += f"SP{servicepack}"
    return v


def _parse_quickfix_dict(root, source_file):
    """Parse a QuickFIX-style <fix> XML dictionary."""
    fields_el = root.find("fields")
    if fields_el is None:
        return None  # not a recognizable QuickFIX dictionary

    major = root.attrib.get("major", "")
    minor = root.attrib.get("minor", "")
    servicepack = root.attrib.get("servicepack")
    version = _normalize_version_string(major, minor, servicepack) if major else None

    fields = {}        # tag -> (field_name, data_type, {enum_value: description})
    name_to_tag = {}

    for field_el in fields_el.findall("field"):
        number = field_el.attrib.get("number")
        name = field_el.attrib.get("name", "")
        ftype = field_el.attrib.get("type", "STRING")
        if not number:
            continue
        values = {}
        for value_el in field_el.findall("value"):
            enum_val = value_el.attrib.get("enum")
            desc = value_el.attrib.get("description", "")
            if enum_val is not None:
                readable = desc.replace("_", " ").title() if desc else enum_val
                values[enum_val] = readable
        fields[number] = (name, ftype, values)
        name_to_tag[name] = number

    def _required_tags(section_el):
        out = {}
        if section_el is None:
            return out
        for f in section_el.findall("field"):
            name = f.attrib.get("name")
            if f.attrib.get("required") == "Y" and name in name_to_tag:
                out[name_to_tag[name]] = name
        return out

    header_required = _required_tags(root.find("header"))
    trailer_required = _required_tags(root.find("trailer"))

    messages = {}  # msgtype -> {"name": ..., "required": [tag, ...]}
    messages_el = root.find("messages")
    if messages_el is not None:
        for msg_el in messages_el.findall("message"):
            msgtype = msg_el.attrib.get("msgtype")
            msgname = msg_el.attrib.get("name", "")
            required_tags = []
            for child in msg_el:
                if child.tag == "field" and child.attrib.get("required") == "Y":
                    fname = child.attrib.get("name")
                    if fname in name_to_tag:
                        required_tags.append(name_to_tag[fname])
                # Note: required fields nested inside / blocks
                # are intentionally not expanded here - only top-level required
                # fields on the message itself are checked.
            if msgtype:
                messages[msgtype] = {"name": msgname, "required": required_tags}

    if version is None:
        base = os.path.basename(source_file).upper()
        m = re.search(r'FIX\.?(\d)\.?(\d)(SP\d)?', base)
        if m:
            version = f"FIX.{m.group(1)}.{m.group(2)}" + (m.group(3) or "")

    return {
        "version": version or os.path.basename(source_file),
        "fields": fields,
        "header_required": header_required,
        "trailer_required": trailer_required,
        "messages": messages,
        "source_file": source_file,
        "schema": "QuickFIX",
    }


def _parse_orchestra_dict(root, source_file):
    """
    Best-effort parse of a FIX Orchestra repository XML file.
    Orchestra exports vary by tool/vendor, so this covers the common shape
    (fields + codeSets) and may not capture every repository's structure.
    """
    if root.tag.startswith("{"):
        uri = root.tag.split("}")[0][1:]
    else:
        uri = ""

    def tag(name):
        return f"{{{uri}}}{name}" if uri else name

    codesets = {}
    for cs in root.iter(tag("codeSet")):
        cs_name = cs.attrib.get("name", "")
        values = {}
        for code in cs.findall(tag("code")):
            val = code.attrib.get("value")
            desc = code.attrib.get("name", code.attrib.get("id", ""))
            if val is not None:
                values[val] = desc.replace("_", " ").title()
        codesets[cs_name] = values

    fields = {}
    for f in root.iter(tag("field")):
        fid = f.attrib.get("id")
        fname = f.attrib.get("name", "")
        ftype = f.attrib.get("type", "String")
        if not fid:
            continue
        values = codesets.get(ftype, {})
        fields[fid] = (fname, ftype.upper(), values)

    if not fields:
        return None  # didn't look like Orchestra after all

    version = root.attrib.get("version") or os.path.basename(source_file)

    return {
        "version": version,
        "fields": fields,
        "header_required": {},
        "trailer_required": {},
        "messages": {},
        "source_file": source_file,
        "schema": "Orchestra (best-effort)",
    }


def load_fix_dictionaries(root_dir):
    """
    Recursively scan root_dir for FIX dictionary XML files, parse each as
    QuickFIX-style or FIX Orchestra, and return (dictionaries, skipped).
    dictionaries is keyed by normalized FIX version string, e.g. 'FIX.4.2'.
    skipped is a list of (filepath, reason) for files that didn't match
    a recognized schema or failed to parse - these are not fatal.
    """
    dictionaries = {}
    skipped = []

    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            if not fname.lower().endswith(".xml"):
                continue
            full_path = os.path.join(dirpath, fname)
            try:
                tree = ET.parse(full_path)
                root = tree.getroot()
            except ET.ParseError as e:
                skipped.append((full_path, f"XML parse error: {e}"))
                continue

            parsed = None
            try:
                root_tag = root.tag.split("}")[-1].lower()
                if root_tag == "fix":
                    parsed = _parse_quickfix_dict(root, full_path)
                elif "repository" in root_tag or root.tag.startswith("{"):
                    parsed = _parse_orchestra_dict(root, full_path)
            except Exception as e:
                skipped.append((full_path, f"Failed to parse as a FIX dictionary: {e}"))
                continue

            if parsed is None:
                skipped.append((full_path, "Root element not recognized as QuickFIX or Orchestra schema"))
                continue

            dictionaries[parsed["version"]] = parsed

    return dictionaries, skipped


# ─────────────────────────────────────────────────────────────────────────────
# PARSER / VALIDATOR (now driven by the loaded dictionary, not hardcoded data)
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
    if tag in fields:
        field_name, ftype, values = fields[tag]
        value_def = values.get(value, "")
        return {"tag": tag, "field_name": field_name, "value": value, "value_def": value_def,
                "description": f"FIX data type: {ftype}", "known": True}
    return {"tag": tag, "field_name": "UNKNOWN TAG", "value": value, "value_def": "",
            "description": f"Tag not defined in {dictionary.get('version', 'loaded')} dictionary", "known": False}


def get_msg_type_label(pairs_dict, dictionary):
    msg_type_val = pairs_dict.get("35")
    if msg_type_val and msg_type_val in dictionary.get("messages", {}):
        return dictionary["messages"][msg_type_val].get("name", msg_type_val)
    fields = dictionary.get("fields", {})
    if "35" in fields:
        return fields["35"][2].get(msg_type_val, f"Unknown MsgType ({msg_type_val})")
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
        errors.append(f"Tag 35 (MsgType) = '{msg_type_val}' is not defined in the {dictionary.get('version')} dictionary")

    for tag, value in pairs:
        if tag not in fields:
            continue
        field_name, ftype, _ = fields[tag]
        dtype = QUICKFIX_TYPE_MAP.get(ftype.upper(), "STRING")
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
        tag, field_name, value, value_def = r["tag"], r["field_name"], r["value"], r["value_def"]
        if value_def:
            lines.append(f"  Tag {tag:>4} | {field_name:<30} = {value}")
            lines.append(f"           {'':30}   -> {value_def}")
        else:
            lines.append(f"  Tag {tag:>4} | {field_name:<30} = {value}")
        if tag == "55":
            lines.append(f"           {'':30}   -> {annotate_symbol_tag(value)}")
        if not r["known"]:
            lines.append(f"           WARNING: {r['description']}")

    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="FIX Protocol Parser - loads tag/message definitions dynamically from a repository of FIX dictionary XML files."
    )
    parser.add_argument("dict_dir", help="Path to the directory containing FIX dictionary XML files")
    parser.add_argument("input_file", help="Path to the file containing FIX messages to parse")
    parser.add_argument("output_file", help="Path to write the parsed output")
    args = parser.parse_args()

    if not os.path.isdir(args.dict_dir):
        print(f"Error: dictionary directory '{args.dict_dir}' not found.")
        sys.exit(1)
    if not os.path.exists(args.input_file):
        print(f"Error: input file '{args.input_file}' not found.")
        sys.exit(1)

    print(f"\n{'=' * 70}")
    print(f"  FIX Protocol Parser - Dynamic Dictionary Edition")
    print(f"  Dictionary repo : {args.dict_dir}")
    print(f"  Input           : {args.input_file}")
    print(f"  Output          : {args.output_file}")
    print(f"{'=' * 70}\n")

    print("Scanning repository for FIX dictionary files...")
    dictionaries, skipped = load_fix_dictionaries(args.dict_dir)

    if not dictionaries:
        print(f"\nERROR: No valid FIX dictionary files found in '{args.dict_dir}'.")
        print("Expected QuickFIX-style XML (root <fix major=.. minor=..> with a <fields> section)")
        print("or a FIX Orchestra repository XML file.")
        sys.exit(1)

    print(f"\nLoaded {len(dictionaries)} FIX dictionary version(s):")
    for v, d in sorted(dictionaries.items()):
        rel = os.path.relpath(d["source_file"], args.dict_dir)
        print(f"  - {v:<14} {len(d['fields']):>4} fields, {len(d['messages']):>3} message types  [{d['schema']}]  ({rel})")

    if skipped:
        print(f"\nSkipped {len(skipped)} file(s) not recognized as a FIX dictionary:")
        for path, reason in skipped:
            rel = os.path.relpath(path, args.dict_dir)
            print(f"  - {rel}: {reason}")
    print()

    msg_count, skip_count, error_msg_count = 0, 0, 0
    asset_class_counts = {}
    version_counts = {}
    output_lines = [
        f"FIX Protocol Parser - Dynamic Dictionary Edition - Output\n"
        f"Dictionary Repo : {args.dict_dir}\n"
        f"Input File      : {args.input_file}\n"
        f"Generated       : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
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
            dictionary = {"fields": {}, "header_required": {}, "trailer_required": {}, "messages": {}, "version": "UNKNOWN"}
        elif version not in dictionaries:
            available = ", ".join(sorted(dictionaries.keys()))
            version_note = f"No dictionary loaded for FIX version '{version}' (available: {available}) - tags shown without definitions"
            dictionary = {"fields": {}, "header_required": {}, "trailer_required": {}, "messages": {}, "version": version}
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
                print(f"  Tag {tag:>4} | {field_name:<30} = {value}")
                print(f"           {'':30}   -> {value_def}")
            else:
                print(f"  Tag {tag:>4} | {field_name:<30} = {value}")
            if tag == "55":
                print(f"           {'':30}   -> {annotate_symbol_tag(value)}")
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