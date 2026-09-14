import json
import os
import re
import base64
import io
import csv
from datetime import datetime
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')   # must be before pyplot import - Lambda has no display
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# TIMESTAMP PARSING
# ─────────────────────────────────────────────────────────────────────────────

TS_FORMATS = [
    "%Y%m%d-%H:%M:%S.%f",           # FIX standard:   20240101-09:30:00.123456
    "%Y%m%d-%H:%M:%S",              # FIX no-millis:  20240101-09:30:00
    "%Y-%m-%d %H:%M:%S.%f",         # ISO space:      2024-01-01 09:30:00.123
    "%Y-%m-%d %H:%M:%S",            # ISO no-millis:  2024-01-01 09:30:00
    "%Y/%m/%d %H:%M:%S.%f",         # Slash ISO
    "%Y/%m/%d %H:%M:%S",
    "%H:%M:%S.%f",                  # Time-only:      09:30:00.123
    "%H:%M:%S",
]

# Regex patterns to pull a timestamp from the START of a log line prefix
LOG_TS_PATTERNS = [
    r'(\d{8}-\d{2}:\d{2}:\d{2}(?:\.\d+)?)',        # 20240101-09:30:00.123
    r'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)',  # 2024-01-01 09:30:00
    r'(\d{4}/\d{2}/\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)',  # 2024/01/01 09:30:00
    r'(\d{2}:\d{2}:\d{2}(?:\.\d+)?)',               # 09:30:00.123  (time-only)
]


def parse_ts(ts_str):
    """Try each known format until one parses. Returns datetime or None."""
    ts_str = ts_str.strip()
    for fmt in TS_FORMATS:
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None


def extract_tag(message, tag):
    """Extract value for a given tag number from a FIX message string."""
    for delim in ('\x01', '|', '^'):
        m = re.search(r'(?:^|\' + delim + r\')' + str(tag) + r'=([^' + delim + r']+)', message)
        if m:
            return m.group(1).strip()
    # Fallback: simple regex (handles mixed delimiters)
    m = re.search(r'(?:^|[\x01|^])' + str(tag) + r'=([^\x01|^]+)', message)
    return m.group(1).strip() if m else None


# ─────────────────────────────────────────────────────────────────────────────
# LOG PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_fix_log(content):
    """
    Parse a FIX log file line by line.

    Latency = log_timestamp - SendingTime (tag 52)
    This captures the delay between when the counterparty sent the message
    and when your system logged/received it.

    Returns a list of record dicts.
    """
    records = []
    lines = content.splitlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Locate start of FIX message in the line
        fix_start = -1
        for marker in ('8=FIX.', '8=FIXT.'):
            idx = line.find(marker)
            if idx != -1:
                fix_start = idx
                break
        if fix_start == -1:
            continue

        fix_msg = line[fix_start:]
        prefix  = line[:fix_start]

        # Pull log timestamp from the line prefix
        log_time = None
        for pattern in LOG_TS_PATTERNS:
            m = re.search(pattern, prefix)
            if m:
                log_time = parse_ts(m.group(1))
                if log_time:
                    break

        if log_time is None:
            continue

        # Extract key FIX fields
        sending_time_str = extract_tag(fix_msg, 52)
        sender   = extract_tag(fix_msg, 49) or 'UNKNOWN'
        target   = extract_tag(fix_msg, 56) or 'UNKNOWN'
        msg_type = extract_tag(fix_msg, 35) or ''

        if not sending_time_str:
            continue

        send_time = parse_ts(sending_time_str)
        if send_time is None:
            continue

        # If SendingTime was time-only (year=1900), inject log date
        if send_time.year == 1900:
            send_time = send_time.replace(
                year=log_time.year, month=log_time.month, day=log_time.day
            )

        latency_ms = (log_time - send_time).total_seconds() * 1000.0

        # Discard obviously invalid values (negative or > 60 s)
        if -500 <= latency_ms <= 60000:
            records.append({
                'log_time':   log_time,
                'send_time':  send_time,
                'latency_ms': latency_ms,
                'sender':     sender,
                'target':     target,
                'msg_type':   msg_type,
                'pair':       f'{sender} -> {target}',
            })

    return records


# ─────────────────────────────────────────────────────────────────────────────
# MATPLOTLIB CHART
# ─────────────────────────────────────────────────────────────────────────────

CHART_COLORS = [
    '#3b6fea', '#f59e0b', '#10b981', '#ef4444',
    '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16',
]


def generate_plot(records):
    """
    Plot FIX latency (ms) against log timestamp, one series per CompID pair.
    Returns a base64-encoded PNG string.
    """
    pairs = defaultdict(list)
    for r in records:
        pairs[r['pair']].append((r['log_time'], r['latency_ms']))

    fig, ax = plt.subplots(figsize=(13, 6))
    fig.patch.set_facecolor('#0f1117')
    ax.set_facecolor('#16181f')

    for i, (pair, data) in enumerate(sorted(pairs.items())):
        data.sort(key=lambda x: x[0])
        times     = [d[0] for d in data]
        latencies = [d[1] for d in data]
        color = CHART_COLORS[i % len(CHART_COLORS)]
        ax.plot(
            times, latencies, 'o-',
            label=pair, color=color,
            linewidth=1.3, markersize=3, alpha=0.88
        )

    # Formatting
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha='right')

    ax.set_xlabel('Log Timestamp',  color='#a0a8c0', fontsize=11)
    ax.set_ylabel('Latency (ms)',   color='#a0a8c0', fontsize=11)
    ax.set_title('FIX Message Latency by CompID Pair',
                 color='#e8e8e8', fontsize=13, fontweight='bold', pad=12)

    for spine in ax.spines.values():
        spine.set_color('#2a2d3a')
    ax.tick_params(colors='#6a6a8a', labelsize=9)
    ax.grid(True, color='#2a2d3a', linestyle='--', alpha=0.5)
    ax.legend(
        loc='upper right',
        facecolor='#1a1d27', edgecolor='#2a2d3a',
        labelcolor='#e8e8e8', fontsize=9
    )

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


# ─────────────────────────────────────────────────────────────────────────────
# STATISTICS & CSV
# ─────────────────────────────────────────────────────────────────────────────

def compute_summary(records):
    """Return min/max/mean/median/p95 per CompID pair using numpy."""
    pairs = defaultdict(list)
    for r in records:
        pairs[r['pair']].append(r['latency_ms'])

    summary = []
    for pair, latencies in sorted(pairs.items()):
        arr = np.array(latencies)
        summary.append({
            'pair':      pair,
            'count':     int(len(arr)),
            'min_ms':    float(round(float(np.min(arr)),        3)),
            'max_ms':    float(round(float(np.max(arr)),        3)),
            'mean_ms':   float(round(float(np.mean(arr)),       3)),
            'median_ms': float(round(float(np.median(arr)),     3)),
            'p95_ms':    float(round(float(np.percentile(arr, 95)), 3)),
        })
    return summary


def build_csv(summary):
    """Serialize summary list to a CSV string."""
    buf = io.StringIO()
    fieldnames = ['pair', 'count', 'min_ms', 'max_ms', 'mean_ms', 'median_ms', 'p95_ms']
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(summary)
    return buf.getvalue()


def build_terminal_output(records, summary):
    """Build a terminal-style text block (shown on the website and mimics CLI output)."""
    lines = [
        '=' * 62,
        '  FIX LATENCY ANALYSIS RESULTS',
        f'  Total messages analysed : {len(records)}',
        '=' * 62,
    ]
    for s in summary:
        lines += [
            '',
            f"  Pair     : {s['pair']}",
            f"  Count    : {s['count']}",
            f"  Min      : {s['min_ms']} ms",
            f"  Max      : {s['max_ms']} ms",
            f"  Mean     : {s['mean_ms']} ms",
            f"  Median   : {s['median_ms']} ms",
            f"  P95      : {s['p95_ms']} ms",
        ]
    lines += ['', '=' * 62]
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# LAMBDA HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def _response(status_code, body_dict):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type':                'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers':'Content-Type',
            'Access-Control-Allow-Methods':'POST,OPTIONS',
        },
        'body': json.dumps(body_dict),
    }


def lambda_handler(event, context):
    try:
        method = event.get('requestContext', {}).get('http', {}).get('method', '')
        if method == 'OPTIONS':
            return _response(200, {})

        body = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            body = base64.b64decode(body).decode('utf-8')

        data = json.loads(body)
        file_b64 = (data.get('content') or '').strip()

        if not file_b64:
            return _response(400, {'error': 'No file content provided.'})

        # Decode base64 file sent from the browser
        try:
            log_content = base64.b64decode(file_b64).decode('utf-8', errors='replace')
        except Exception:
            log_content = file_b64

        records = parse_fix_log(log_content)

        if not records:
            return _response(400, {
                'error': (
                    'No parseable FIX messages found with both a log-line timestamp '
                    'and SendingTime (tag 52). Check that the file contains '
                    'timestamped FIX log lines.'
                )
            })

        summary         = compute_summary(records)
        plot_b64        = generate_plot(records)
        csv_content     = build_csv(summary)
        terminal_output = build_terminal_output(records, summary)

        # Print to Lambda logs (visible in CloudWatch, mimics CLI terminal output)
        print(terminal_output)

        return _response(200, {
            'records_parsed':  len(records),
            'plot_png_b64':    plot_b64,
            'summary':         summary,
            'csv_content':     csv_content,
            'terminal_output': terminal_output,
        })

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(f'ERROR: {exc}\n{tb}')
        return _response(500, {'error': f'Unexpected error: {str(exc)}'})