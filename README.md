# FIX Support AI Engine

A production-grade FIX Protocol support platform deployed on AWS.
Eight free tools for capital markets technologists — no login required.

**Live Demo:** https://dpzvr5uxtdy4g.cloudfront.net

---

## Tools

| Tool | Description |
|---|---|
| **FIX Message Parser** | Decodes any pipe-delimited FIX message (4.0 → 5.0 SP2) against the official FIX Trading Community Repository. Native Coinbase, Kraken and Talos crypto dialect support. |
| **FIX Session Generator** | Generates copy-ready Market Data and Order Session connectivity specs for client onboarding. |
| **FIX Latency Analyser** | Uploads a FIX log and plots session latency per CompID pair with Min/Max/Mean/Median/P95 stats and downloadable CSV. |
| **Trade Latency Analyser** | Matches 35=D orders to 35=8 fills via ClOrdID and plots round-trip execution latency per venue — scatter chart + box plot distribution. |
| **OMS/EMS Log Search** | Searches any log file (pipe/CSV/tab/text) with 10 lines of context around every match. FIX messages auto-decoded inline. |
| **STP Generator** | Converts a 35=8 Execution Report fill (150=F, 39=1/2) to a complete 35=AE Trade Capture Report with full field derivation map and settlement break risk flagging. |
| **Portfolio Analyser** | Uploads a holdings CSV or a FIX log of 35=8 fills, converts every position to one base currency with live or manual FX rates, and rolls up by account, symbol, asset class and currency — with concentration, loss and data-quality alerts. |
| **Unix / Linux CLI Support** | 87 copy-ready commands for production support on trading platforms — fill in your log path, CompIDs and host once and every command updates. Includes a FIX Log Grep Builder and step-by-step triage playbooks. |

---

## Unix / Linux CLI Support

A command-line toolkit for support engineers debugging FIX, OMS / EMS and
trading platforms in production. Runs entirely in the browser — no backend.

| Category | What's covered |
|---|---|
| **Find & Read Logs** | `tail -F`, `less +F`, time-window extracts, rotated `.gz` logs, `journalctl` |
| **Search (grep)** | Context lines, multi-term, recursive, `zgrep`, top repeating errors, errors per minute |
| **FIX Logs** | Logons / logouts, session & business rejects, rejected orders, fills, full order lifecycle by ClOrdID, MsgType breakdown, sequence-gap detection |
| **Network & Connectivity** | `nc` / bash port tests, `openssl` TLS & cert expiry, `ss`, `lsof`, `tcpdump`, `mtr`, DNS and routing |
| **System** | Load, CPU, memory, disk, OOM kills, file-descriptor limits, Java thread dumps, NTP clock sync |
| **Config Files** | QuickFIX session blocks, timestamped backups, `diff` across servers, stored sequence numbers |

**Also includes**

- **Your Environment bar** — enter `{LOG}`, `{CFG}`, `{SENDER}`, `{TARGET}`, `{HOST}`, `{PORT}`, `{ID}` and `{PROC}` once; every command fills in automatically (saved in your browser only).
- **FIX Log Grep Builder** — pick a MsgType (35=A, 35=8, 35=AE…), add tag filters such as `39=8`, choose follow-live / gzipped / count-only, and get the exact one-liner.
- **Triage playbooks** — session won't log on, sequence number mismatch, missing order / fill, disk full, high latency.
- **Search, category filters and a printable cheat sheet.**

Commands target Linux servers (RHEL / Rocky 7+, Ubuntu 14.04+, Debian 8+).
A few differ on macOS — e.g. use `top -o cpu` instead of `top -c`.

---

## Architecture

All infrastructure is defined as Terraform IaC — fully reproducible from scratch.

---

## Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) configured with credentials
  (`aws configure`)
- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5
- Python 3.12+ and pip3 (for the matplotlib Lambda layer build)
- An AWS account with permissions to create:
  Lambda, API Gateway, S3, CloudFront, IAM roles

---

## Quick Start

**1. Clone the repository**
```bash
git clone https://github.com/stevetest1-learn/fix-support-ai-engine.git
cd fix-support-ai-engine
```

**2. Download the FIX Repository**

The official FIX Trading Community Repository XML files are NOT included
in this repo (they are large and version-specific). Download them from
https://www.fixtrading.org/standards/fix-repository/ and place them in:


**3. Deploy to AWS**
```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # then edit the path inside
terraform init
terraform plan
terraform apply
```

**4. Get your live URL**
```bash
terraform output frontend_url
```

---

## Project Structure


---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | HTML / CSS / Vanilla JavaScript |
| Backend | Python 3.12 on AWS Lambda |
| API | AWS API Gateway HTTP API v2 |
| CDN | AWS CloudFront + S3 |
| IaC | Terraform >= 1.5 |
| Charts | matplotlib + numpy (Lambda Layer via S3) |
| FIX Spec | Official FIX Trading Community Repository (4.0 → 5.0 SP2) |

---

## Supported FIX Versions

FIX 4.0 · FIX 4.1 · FIX 4.2 · FIX 4.3 · FIX 4.4 · FIX 5.0 · FIX 5.0 SP1 · FIX 5.0 SP2

## Supported Crypto Dialects

| Venue | Custom Tags |
|---|---|
| Coinbase Advanced Trade | 7928 (SelfTradePrevention), 8013 (CancelAfter), 8014 (PostOnly), 9001–9004 |
| Kraken | 9001–9011 (order types, leverage, fees), 6001 (close order type) |
| Talos | 1301 (MarketID / venue routing), 7001–7018 (algorithms, SOR policy, fees) |

---

## Contributing

Pull requests are welcome. For major changes please open an issue first.

1. Fork the repo
2. Create your feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

---

## Author

**Steve Hatfield** — FIX Protocol Specialist, 20+ years in capital markets technology

- 📞 732-232-4919
- 📧 stevenhatfield23@gmail.com
- 🔗 [linkedin.com/in/823stevehatfield](https://linkedin.com/in/823stevehatfield)
- 🐙 [github.com/stevetest1-learn](https://github.com/stevetest1-learn)

---

## Support the Project

If these tools have helped you in production, please consider a small
donation to keep the servers running:

👉 [Donate via PayPal](https://www.paypal.com/ncp/payment/LWWLG6BHRDH58)

---

## License

[MIT](LICENSE)
