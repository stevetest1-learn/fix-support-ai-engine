import json
import re
import base64
import io
import csv
from datetime import datetime
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# TIMESTAMP HELPERS  (identical to latency analyser)
# ─────────────────────────────────────────────────────────────────────────────

TS_FORMATS = [
    "%Y%m%d-%H:%M:%S.%f", "%Y%m%d-%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S",
    "%H:%M:%S.%f", "%H:%M:%S",
]
LOG_TS_PATTERNS = [
    r'(\d{8}-\d{2}:\d{2}:\d{2}(?:\.\d+)?)',
    r'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)',
    r'(\d{4}/\d{2}/\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)',
    r'(\d{2}:\d{2}:\d{2}(?:\.\d+)?)',
]

EXEC_TYPE_LABELS = {'1': 'Partial Fill', '2': 'Fill'}
SIDE_LABELS      = {'1': 'Buy', '2': 'Sell', '5': 'Sell Short', '6': 'Sell Short Exempt'}
CHART_COLORS     = ['#3b6fea','#f59e0b','#10b981','#ef4444','#8b5cf6','#ec4899','#06b6d4','#84cc16']


def parse_ts(ts_str):
    if not ts_str:
        return None
    ts_str = ts_str.strip()
    for fmt in TS_FORMATS:
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None


def extract_log_time(prefix):
    for pat in LOG_TS_PATTERNS:
        m = re.search(pat, prefix)
        if m:
            t = parse_ts(m.group(1))
            if t:
                return t
    return None


def extract_tag(message, tag):
    m = re.search(r'(?:^|[\x01|^])' + str(tag) + r'=([^\x01|^]+)', message)
    return m.group(1).strip() if m else None


def fix_year(ts, ref):
    """If ts was parsed from a time-only string, inject the date from ref."""
    if ts and ref and ts.year == 1900:
        return ts.replace(year=ref.year, month=ref.month, day=ref.day)
    return ts


# ─────────────────────────────────────────────────────────────────────────────
# TRADE-MATCHING ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def parse_trade_log(content):
    """
    Single-pass parse of a FIX log file.

    - New Order Singles  (35=D)  are stored in an orders dict keyed by ClOrdID.
    - Execution Reports  (35=8)  with ExecType 150=1 (partial fill) or 150=2
      (fill), AND/OR OrdStatus 39=1 or 39=2 are matched back to their order
      via ClOrdID (tag 11), with OrigClOrdID (tag 41) as a fallback.

    Trade Latency = fill SendingTime (tag 52) - order SendingTime (tag 52)
    This represents the true round-trip from order submission to fill receipt.
    """
    orders  = {}   # ClOrdID -> order record
    trades  = []   # matched pairs

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        # Find FIX message start
        fix_start = -1
        for marker in ('8=FIX.', '8=FIXT.'):
            idx = line.find(marker)
            if idx != -1:
                fix_start = idx
                break
        if fix_start == -1:
            continue

        fix_msg    = line[fix_start:]
        prefix     = line[:fix_start]
        log_time   = extract_log_time(prefix)
        msg_type   = extract_tag(fix_msg, 35)
        send_time  = fix_year(parse_ts(extract_tag(fix_msg, 52)), log_time)
        cl_ord_id  = extract_tag(fix_msg, 11)
        sender     = extract_tag(fix_msg, 49) or 'UNKNOWN'
        target     = extract_tag(fix_msg, 56) or 'UNKNOWN'
        symbol     = extract_tag(fix_msg, 55) or ''

        # ── New Order Single ─────────────────────────────────────────────────
        if msg_type == 'D':
            order_ts = send_time or log_time
            if cl_ord_id and order_ts:
                orders[cl_ord_id] = {
                    'order_ts':  order_ts,
                    'log_time':  log_time,
                    'sender':    sender,
                    'target':    target,
                    'symbol':    symbol,
                    'side':      extract_tag(fix_msg, 54) or '',
                    'order_qty': extract_tag(fix_msg, 38) or '',
                    'ord_type':  extract_tag(fix_msg, 40) or '',
                    'price':     extract_tag(fix_msg, 44) or '',
                }

        # ── Execution Report ─────────────────────────────────────────────────
        elif msg_type == '8':
            exec_type  = extract_tag(fix_msg, 150) or ''
            ord_status = extract_tag(fix_msg, 39)  or ''

            is_fill = exec_type in ('1', '2') or ord_status in ('1', '2')
            if not is_fill:
                continue

            # Match via ClOrdID first, then OrigClOrdID (tag 41)
            orig_cl_ord_id = extract_tag(fix_msg, 41)
            order = None
            matched_id = None
            for cid in filter(None, [cl_ord_id, orig_cl_ord_id]):
                if cid in orders:
                    order = orders[cid]
                    matched_id = cid
                    break

            if order is None:
                continue

            fill_ts = send_time or log_time
            if not fill_ts or not order['order_ts']:
                continue

            latency_ms = (fill_ts - order['order_ts']).total_seconds() * 1000.0

            # Filter out clearly invalid readings (negative or >60s)
            if not (0 <= latency_ms <= 60000):
                continue

            trades.append({
                'order_ts':    order['order_ts'],
                'fill_ts':     fill_ts,
                'latency_ms':  round(latency_ms, 3),
                'sender':      order['sender'],
                'target':      order['target'],
                'pair':        f"{order['sender']} -> {order['target']}",
                'symbol':      symbol or order['symbol'],
                'side':        SIDE_LABELS.get(order['side'], order['side']),
                'exec_type':   EXEC_TYPE_LABELS.get(exec_type, exec_type),
                'ord_status':  ord_status,
                'last_px':     extract_tag(fix_msg, 31) or '',
                'last_shares': extract_tag(fix_msg, 32) or '',
                'cum_qty':     extract_tag(fix_msg, 14) or '',
                'leaves_qty':  extract_tag(fix_msg, 151) or '',
                'cl_ord_id':   matched_id,
            })

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# CHART  (two subplots: time-series scatter + box plot distribution)
# ─────────────────────────────────────────────────────────────────────────────

def generate_plot(trades):
    pairs = defaultdict(list)
    for t in trades:
        pairs[t['pair']].append(t)

    sorted_pairs = sorted(pairs.keys())
    fig = plt.figure(figsize=(13, 9), facecolor='#0f1117')
    gs  = fig.add_gridspec(2, 1, height_ratios=[2, 1], hspace=0.38)

    ax_scatter = fig.add_subplot(gs[0])
    ax_box     = fig.add_subplot(gs[1])

    for ax in (ax_scatter, ax_box):
        ax.set_facecolor('#16181f')
        for spine in ax.spines.values():
            spine.set_color('#2a2d3a')
        ax.tick_params(colors='#6a6a8a', labelsize=9)
        ax.grid(True, color='#2a2d3a', linestyle='--', alpha=0.5)

    # ── Top: scatter latency vs order time ───────────────────────────────────
    for i, pair in enumerate(sorted_pairs):
        pts   = sorted(pairs[pair], key=lambda x: x['order_ts'])
        times = [p['order_ts']   for p in pts]
        lats  = [p['latency_ms'] for p in pts]
        color = CHART_COLORS[i % len(CHART_COLORS)]
        ax_scatter.scatter(times, lats, label=pair, color=color,
                           s=25, alpha=0.82, zorder=3)
        ax_scatter.plot(times, lats, color=color, linewidth=0.8, alpha=0.45)

    ax_scatter.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    ax_scatter.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax_scatter.xaxis.get_majorticklabels(), rotation=25, ha='right',
             color='#6a6a8a')
    ax_scatter.set_xlabel('Order SendingTime (tag 52)', color='#a0a8c0', fontsize=10)
    ax_scatter.set_ylabel('Trade Latency (ms)',         color='#a0a8c0', fontsize=10)
    ax_scatter.set_title('Trade Latency: Order Sent → Fill Received  '
                         '(ExecType 150=1/2  ·  OrdStatus 39=1/2)',
                         color='#e8e8e8', fontsize=12, fontweight='bold', pad=10)
    ax_scatter.legend(loc='upper right', facecolor='#1a1d27',
                      edgecolor='#2a2d3a', labelcolor='#e8e8e8', fontsize=9)

    # ── Bottom: horizontal box plot per pair ──────────────────────────────────
    box_data   = [np.array([t['latency_ms'] for t in pairs[p]]) for p in sorted_pairs]
    bp = ax_box.boxplot(
        box_data, vert=False, patch_artist=True,
        medianprops=dict(color='#fff', linewidth=1.5),
        whiskerprops=dict(color='#6a6a8a'),
        capprops=dict(color='#6a6a8a'),
        flierprops=dict(marker='o', color='#ef4444', markersize=3, alpha=0.6),
    )
    for patch, color in zip(bp['boxes'], [CHART_COLORS[i % len(CHART_COLORS)]
                                           for i in range(len(sorted_pairs))]):
        patch.set_facecolor(color)
        patch.set_alpha(0.55)

    ax_box.set_yticks(range(1, len(sorted_pairs) + 1))
    ax_box.set_yticklabels(sorted_pairs, color='#a0a8c0', fontsize=9)
    ax_box.set_xlabel('Trade Latency (ms)', color='#a0a8c0', fontsize=10)
    ax_box.set_title('Latency Distribution per Venue Pair',
                     color='#e8e8e8', fontsize=11, pad=8)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


# ─────────────────────────────────────────────────────────────────────────────
# STATISTICS & CSV
# ─────────────────────────────────────────────────────────────────────────────

def compute_summary(trades):
    pairs = defaultdict(list)
    for t in trades:
        pairs[t['pair']].append(t['latency_ms'])

    summary = []
    for pair, lats in sorted(pairs.items()):
        arr = np.array(lats)
        summary.append({
            'pair':      pair,
            'count':     int(len(arr)),
            'min_ms':    float(round(float(np.min(arr)),         3)),
            'max_ms':    float(round(float(np.max(arr)),         3)),
            'mean_ms':   float(round(float(np.mean(arr)),        3)),
            'median_ms': float(round(float(np.median(arr)),      3)),
            'p95_ms':    float(round(float(np.percentile(arr,95)),3)),
        })
    return summary


def build_csv(trades):
    buf = io.StringIO()
    fields = ['cl_ord_id','pair','symbol','side','exec_type',
              'order_ts','fill_ts','latency_ms',
              'last_px','last_shares','cum_qty','leaves_qty']
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for t in trades:
        row = dict(t)
        row['order_ts'] = t['order_ts'].strftime('%Y%m%d-%H:%M:%S.%f') if t['order_ts'] else ''
        row['fill_ts']  = t['fill_ts'].strftime('%Y%m%d-%H:%M:%S.%f')  if t['fill_ts']  else ''
        writer.writerow(row)
    return buf.getvalue()


def build_terminal(trades, summary):
    lines = [
        '=' * 64,
        '  FIX TRADE LATENCY ANALYSIS',
        f'  Matched order/fill pairs : {len(trades)}',
        '=' * 64,
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
    lines += ['', '=' * 64]
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# LAMBDA HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def _resp(code, body):
    return {
        'statusCode': code,
        'headers': {
            'Content-Type':                'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers':'Content-Type',
            'Access-Control-Allow-Methods':'POST,OPTIONS',
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

        data      = json.loads(body)
        file_b64  = (data.get('content') or '').strip()
        if not file_b64:
            return _resp(400, {'error': 'No file content provided.'})

        try:
            log_content = base64.b64decode(file_b64).decode('utf-8', errors='replace')
        except Exception:
            log_content = file_b64

        trades = parse_trade_log(log_content)

        if not trades:
            return _resp(400, {
                'error': (
                    'No matched order/fill pairs found. '
                    'The log must contain New Order Singles (35=D) and '
                    'Execution Reports (35=8) with ExecType 150=1 or 2 '
                    'and/or OrdStatus 39=1 or 2, sharing a ClOrdID (tag 11).'
                )
            })

        summary  = compute_summary(trades)
        plot_b64 = generate_plot(trades)
        csv_data = build_csv(trades)
        terminal = build_terminal(trades, summary)

        print(terminal)   # appears in CloudWatch logs

        # Serialise datetime objects before JSON dump
        trades_out = []
        for t in trades:
            row = dict(t)
            row['order_ts'] = t['order_ts'].strftime('%Y%m%d-%H:%M:%S.%f') if t['order_ts'] else ''
            row['fill_ts']  = t['fill_ts'].strftime('%Y%m%d-%H:%M:%S.%f')  if t['fill_ts']  else ''
            trades_out.append(row)

        return _resp(200, {
            'trades_matched':  len(trades),
            'plot_png_b64':    plot_b64,
            'summary':         summary,
            'trades':          trades_out,
            'csv_content':     csv_data,
            'terminal_output': terminal,
        })

    except Exception as exc:
        import traceback
        print(traceback.format_exc())
        return _resp(500, {'error': f'Unexpected error: {str(exc)}'})