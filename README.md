# FIX Support AI Engine

A production-grade FIX Protocol support platform deployed on AWS.
Seven free tools for capital markets technologists — no login required.

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
| **Live Coinbase Market Data** | Real-time Bid/Ask prices via the Coinbase Exchange public REST API with auto-refresh and 24hr stats. |
| **STP Generator** | Converts a 35=8 Execution Report fill (150=F, 39=1/2) to a complete 35=AE Trade Capture Report with full field derivation map and settlement break risk flagging. |

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
terraform init
terraform plan -var="repository_source_dir=/path/to/your/repository"
terraform apply -var="repository_source_dir=/path/to/your/repository"
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

👉 [Donate via PayPal](https://paypal.me/YOURPAYPALNAME)

---

## License

[MIT](LICENSE)
