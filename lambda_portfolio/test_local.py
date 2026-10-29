"""
Run the Lambda locally against a file.

  python3 test_local.py sample_multi_ccy.csv
  python3 test_local.py sample_multi_ccy.csv --base EUR
  python3 test_local.py sample_multi_ccy.csv --rate EUR=1.08 --rate GBP=1.27 --rate JPY=0.0066
  python3 test_local.py sample_fills.log
"""
import argparse, base64, json
from lambda_portfolio import lambda_handler

ap = argparse.ArgumentParser()
ap.add_argument("file")
ap.add_argument("--base", default="USD")
ap.add_argument("--rate", action="append", default=[], help="CCY=units of base per 1 CCY")
ap.add_argument("--no-live", action="store_true")
args = ap.parse_args()

with open(args.file, "rb") as f:
    content = base64.b64encode(f.read()).decode()
rates = {k: float(v) for k, v in (r.split("=") for r in args.rate)}
event = {"requestContext": {"http": {"method": "POST"}},
         "body": json.dumps({"content": content, "base_currency": args.base,
                             "fx_rates": rates, "live_rates": not args.no_live})}
resp = lambda_handler(event, None)
d = json.loads(resp["body"])
print("HTTP", resp["statusCode"])
if resp["statusCode"] != 200:
    print(d); raise SystemExit(1)

b = d["summary"]["base_currency"]
print("Source:", d["source"])
print("\nFX rates ->", b)
for r in d["fx"]["rates"]:
    print(f"  {r['currency']}  {r['rate_to_base']:<12} ({r['source']})")
if d["fx"]["missing"]:
    print("  MISSING:", d["fx"]["missing"])
s = d["summary"]
print(f"\nFIRM ({b}): MV {s['market_value']:,.2f} | gross {s['gross_exposure']:,.2f} | "
      f"long {s['long']:,.2f} | short {s['short']:,.2f} | P&L {s['unrealised_pnl']:,.2f}")
print(f"  {s['positions']} positions, {s['accounts']} accounts, {s['symbols']} symbols, "
      f"{s['unconverted_positions']} unconverted")

def table(title, rows, key):
    print(f"\n{title}")
    for r in rows:
        print(f"  {str(r[key]):<14} MV {r['market_value']:>14,.2f}  P&L {r['unrealised_pnl']:>12,.2f}  ({r['positions']} pos)")
table("Accounts", d["accounts"], "account")
table("Symbols (across accounts)", d["symbols"], "symbol")
table("Asset class", d["exposure"]["asset_class"], "asset_class")
table("Currency", d["exposure"]["currency"], "currency")
print("\nAlerts:")
for x in d["alerts"]:
    print(f"  [{x['severity']}] {x.get('account','')} {x.get('symbol','')}: {x['issue']}")
