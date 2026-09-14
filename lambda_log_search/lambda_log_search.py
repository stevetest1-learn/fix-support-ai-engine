"""
FIX Support AI Engine — Log Search Lambda

Accepts an OMS / EMS / FIX log file (any delimiter: pipe, comma, quote, tab,
or plain text) and a set of search terms. For every matching line it returns
10 lines of context above and below, with delimiter detection and structured
field parsing so the frontend can render results in a clean, readable format.

No external dependencies — pure Python stdlib. No Lambda layer required.
"""

import json
import re
import base64
import csv
import io


# ─────────────────────────────────────────────────────────────────────────────
# DELIMITER & FORMAT DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_line_format(line):
    """
    Inspect a single log line and return its format metadata.
    Returns a dict: { delimiter, format, fields }
    """
    stripped = line.strip()

    # FIX protocol message (pipe or SOH delimited tag=value pairs)
    if re.search(r'8=FIX[T]?\.\d', stripped):
        delim = '|' if '|' in stripped else ('^' if '^' in stripped else '\x01')
        fields = parse_fix_fields(stripped, delim)
        return {'delimiter': delim, 'format': 'FIX', 'fields': fields}

    # Pipe-delimited (non-FIX)
    pipe_count = stripped.count('|')
    if pipe_count >= 2:
        fields = [f.strip() for f in stripped.split('|')]
        return {'delimiter': '|', 'format': 'pipe', 'fields': fields}

    # Tab-delimited
    tab_count = stripped.count('\t')
    if tab_count >= 2:
        fields = [f.strip() for f in stripped.split('\t')]
        return {'delimiter': '\t', 'format': 'tab', 'fields': fields}

    # CSV / comma-delimited (use csv module to handle quoted fields)
    comma_count = stripped.count(',')
    if comma_count >= 2:
        try:
            reader = csv.reader(io.StringIO(stripped))
            fields = next(reader, [])
            if len(fields) >= 3:
                return {'delimiter': ',', 'format': 'csv', 'fields': fields}
        except csv.Error:
            pass

    # Plain text / unstructured
    return {'delimiter': None, 'format': 'text', 'fields': [stripped]}


def parse_fix_fields(line, delim):
    """
    Split a FIX message into a list of {tag, value} dicts.
    Includes a human label for the most common tags.
    """
    COMMON_TAGS = {
        '8': 'BeginString', '9': 'BodyLength', '10': 'CheckSum',
        '11': 'ClOrdID', '14': 'CumQty', '17': 'ExecID',
        '20': 'ExecTransType', '21': 'HandlInst', '34': 'MsgSeqNum',
        '35': 'MsgType', '37': 'OrderID', '38': 'OrderQty',
        '39': 'OrdStatus', '40': 'OrdType', '41': 'OrigClOrdID',
        '44': 'Price', '49': 'SenderCompID', '52': 'SendingTime',
        '54': 'Side', '55': 'Symbol', '56': 'TargetCompID',
        '58': 'Text', '59': 'TimeInForce', '60': 'TransactTime',
        '99': 'StopPx', '100': 'ExDestination', '102': 'CxlRejReason',
        '103': 'OrdRejReason', '108': 'HeartBtInt', '112': 'TestReqID',
        '117': 'QuoteID', '122': 'OrigSendingTime', '141': 'ResetSeqNumFlag',
        '150': 'ExecType', '151': 'LeavesQty', '167': 'SecurityType',
        '198': 'SecondaryOrderID', '262': 'MDReqID', '269': 'MDEntryType',
        '279': 'MDUpdateAction', '336': 'TradingSessionID',
        '373': 'SessionRejectReason', '380': 'BusinessRejectReason',
    }
    MSGTYPE_LABELS = {
        '0': 'Heartbeat', '1': 'Test Request', '2': 'Resend Request',
        '3': 'Reject', '4': 'Sequence Reset', '5': 'Logout',
        '8': 'Execution Report', '9': 'Order Cancel Reject', 'A': 'Logon',
        'D': 'New Order Single', 'E': 'New Order List',
        'F': 'Order Cancel Request', 'G': 'Order Cancel/Replace Request',
        'H': 'Order Status Request', 'V': 'Market Data Request',
        'W': 'Market Data Snapshot', 'X': 'Market Data Incremental Refresh',
    }
    SIDE_LABELS = {'1': 'Buy', '2': 'Sell', '5': 'Sell Short'}
    ORDSTATUS   = {'0': 'New', '1': 'Partially Filled', '2': 'Filled',
                   '4': 'Canceled', '8': 'Rejected', 'A': 'Pending New'}
    EXECTYPE    = {'0': 'New', '1': 'Partial Fill', '2': 'Fill',
                   '4': 'Canceled', '8': 'Rejected', 'D': 'Restated'}

    fields = []
    for part in line.split(delim):
        part = part.strip()
        if '=' not in part:
            continue
        tag, _, value = part.partition('=')
        tag = tag.strip()
        value = value.strip()
        label = COMMON_TAGS.get(tag, f'Tag {tag}')
        # Resolve enumerated values for key fields
        friendly = None
        if tag == '35':
            friendly = MSGTYPE_LABELS.get(value)
        elif tag == '54':
            friendly = SIDE_LABELS.get(value)
        elif tag == '39':
            friendly = ORDSTATUS.get(value)
        elif tag == '150':
            friendly = EXECTYPE.get(value)
        fields.append({
            'tag': tag, 'label': label,
            'value': value, 'friendly': friendly,
        })
    return fields


# ─────────────────────────────────────────────────────────────────────────────
# LOG SEARCH ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def search_log(content, search_terms, case_sensitive=False,
               match_mode='any', context_lines=10, max_results=100):
    """
    Search a log file for lines matching any/all of the given search_terms.

    Parameters
    ----------
    content       : str   — full log file content
    search_terms  : list  — list of term strings to search for
    case_sensitive: bool
    match_mode    : 'any' — line matches if ANY term is found
                    'all' — line matches only if ALL terms are found
    context_lines : int   — lines of context above and below each match
    max_results   : int   — cap on number of matches returned

    Returns (results, total_lines, total_matches_found)
    """
    lines = content.splitlines()
    total_lines = len(lines)

    # Build compiled patterns, gracefully falling back on bad regex
    flags = 0 if case_sensitive else re.IGNORECASE
    patterns = []
    for term in search_terms:
        term = term.strip()
        if not term:
            continue
        try:
            patterns.append((term, re.compile(re.escape(term), flags)))
        except re.error:
            pass

    if not patterns:
        return [], total_lines, 0

    results = []
    total_matches_found = 0

    for i, line in enumerate(lines):

        # Determine which terms match this line
        matched_terms = [t for t, p in patterns if p.search(line)]

        if match_mode == 'all' and len(matched_terms) != len(patterns):
            continue
        if match_mode == 'any' and not matched_terms:
            continue

        total_matches_found += 1
        if len(results) >= max_results:
            continue  # keep counting but stop storing

        # Gather context window
        ctx_start = max(0, i - context_lines)
        ctx_end   = min(total_lines, i + context_lines + 1)

        context = []
        for j in range(ctx_start, ctx_end):
            raw = lines[j]
            fmt = detect_line_format(raw)
            context.append({
                'line_number': j + 1,
                'content':     raw,
                'type':        'match' if j == i else ('above' if j < i else 'below'),
                'format':      fmt['format'],
                'delimiter':   fmt['delimiter'],
                'fields':      fmt['fields'] if fmt['format'] in ('FIX', 'pipe', 'tab', 'csv') else [],
            })

        results.append({
            'match_number':      total_matches_found,
            'match_line_number': i + 1,
            'match_content':     line,
            'matched_terms':     matched_terms,
            'context':           context,
            'format':            detect_line_format(line)['format'],
        })

    return results, total_lines, total_matches_found


# ─────────────────────────────────────────────────────────────────────────────
# LAMBDA HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def _resp(code, body):
    return {
        'statusCode': code,
        'headers': {
            'Content-Type':                'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Methods': 'POST,OPTIONS',
        },
        'body': json.dumps(body),
    }


def lambda_handler(event, context):
    try:
        method = event.get('requestContext', {}).get('http', {}).get('method', '')
        if method == 'OPTIONS':
            return _resp(200, {})

        body = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body = base64.b64decode(body).decode('utf-8')

        data = json.loads(body)

        # ── File content ─────────────────────────────────────────────────────
        file_b64 = (data.get('content') or '').strip()
        if not file_b64:
            return _resp(400, {'error': 'No file content provided.'})

        try:
            log_content = base64.b64decode(file_b64).decode('utf-8', errors='replace')
        except Exception:
            log_content = file_b64

        # ── Search parameters ─────────────────────────────────────────────────
        raw_terms      = data.get('terms') or ''
        # Accept comma-separated list of terms
        search_terms   = [t.strip() for t in raw_terms.split(',') if t.strip()]
        if not search_terms:
            return _resp(400, {'error': 'No search terms provided. Enter one or more comma-separated terms.'})

        case_sensitive = bool(data.get('case_sensitive', False))
        match_mode     = data.get('match_mode', 'any')   # 'any' | 'all'
        context_lines  = int(data.get('context_lines', 10))
        max_results    = min(int(data.get('max_results', 100)), 200)

        context_lines = max(0, min(context_lines, 50))

        results, total_lines, total_matches_found = search_log(
            log_content, search_terms,
            case_sensitive=case_sensitive,
            match_mode=match_mode,
            context_lines=context_lines,
            max_results=max_results,
        )

        truncated = total_matches_found > max_results

        return _resp(200, {
            'total_lines':         total_lines,
            'total_matches_found': total_matches_found,
            'results_returned':    len(results),
            'truncated':           truncated,
            'search_terms':        search_terms,
            'match_mode':          match_mode,
            'case_sensitive':      case_sensitive,
            'context_lines':       context_lines,
            'results':             results,
        })

    except Exception as exc:
        import traceback
        print(traceback.format_exc())
        return _resp(500, {'error': f'Unexpected error: {str(exc)}'})