# Carrier Clearance

An agent that checks a new carrier (truck operator) for an Indian 3PL. Give it a carrier name, a form, or a pasted WhatsApp or email message. It returns a sourced verdict (**Clear**, **Refer**, **Reject** or **Cannot verify**), a vendor card in English and Hindi, and an approver card. Nothing executes after approval: there's no payment, no email and no vendor-master write.

## The job

Before a 3PL gives a load and an advance to a new carrier, someone has to prove the carrier is real, active and who it says it is. Today the 3PL's procurement team pays for this: either staff hours spent checking by hand, or an outside verification firm's fee per vendor. The buyer is the procurement head, who clears new carriers every month.

- **Input:** one onboarding request: form fields, text pasted from an email or WhatsApp message, or just the carrier's name.
- **Output:** a verdict (Clear, Refer, Reject or Cannot verify), every fact linked to the field path or quote it came from and that record's date, a vendor card in English and Hindi that says how to contest, and an approver card with the job's measured cost.
- **After the verdict:** a person approves, and the approval is a row bound to `sha256(verdict + evidence ids + bank hash + rules hash)`. Editing a field or changing the rules voids it.

## Pass mark

- No wrong verdict on the rehearsal set (`tests/cases/`, 9 cases).
- Every fact sourced: a fact without a field path or exact quote in a stored raw response is dropped.
- 20 random `kill -9`s and restarts, each ending in the same verdict, with no paid call repeated (only a call in flight at the kill may go out again) and one approval row after approving twice.
- Every job under 90 seconds and under the ₹25 cost cap (`rules.yaml` `limits`).

## Data sources and what they cost

| Source | Used for | Price (from `rules.yaml` `prices_inr`) |
|---|---|---|
| GST verification (Cashfree `POST /verification/gstin`) | c2 status, legal name, PAN; confirming discovery candidates | ₹1.00 per call (to confirm from the price list) |
| Crustdata: identify, Enrich (`basic_info`, `revenue`, `news`, `hiring`), Web Search, Web Fetch, Person Search | Discovery from a name, age, activity, adverse news, lookalikes | Credits from the `X-Credits-Used` header × rupees per credit (rate to set from the plan) |
| Claude Haiku (`claude-haiku-5-5`) | Parsing pasted text into fields; reading passages. Never decides | ₹8.50 per million input tokens, ₹42.50 per million output |
| ifsc.razorpay.com (public IFSC lookup, not payments) | c9 bank and branch | Free |
| OpenSanctions | c7 sanctions screening of the directors given | Per the API plan (needs `OPENSANCTIONS_API_KEY`, else listed as a gap) |
| Vendor master (`data/vendor_master.csv`), earlier jobs | Changed bank details, duplicate vendors | Free (local) |

Every call is a row in the `calls` table with its rupee cost; the approver card's total is the sum of those rows.

**Measured cost per job:** the latest saved run (`logs/eval.txt`, 22 November 2026) used `MOCK=1` with no live keys, so every priced call was a fixture and the median and worst cost were both ₹0.00. That isn't a real figure. Rerun `scripts/eval.py` with `MOCK=0` and live keys, then write the median and worst rupees per job here before the demo. **Price:** the measured median plus a margin, set against an outside firm's fee per vendor and against one advance lost to a fake carrier. **Limit:** we catch changed bank details and payee-name mismatches, but we don't verify the account holder. That would be a paid add-on, a penny-drop check at about ₹1 to ₹3 per call.

## Run

Everything runs through `uv` with Python 3.12, from `.python-version`:

```sh
cp .env.example .env              # add keys; MOCK=1 uses GST and parse fixtures
uv run uvicorn cc.app:app --reload   # http://127.0.0.1:8000, starts the worker
uv run pytest tests/              # unit tests
uv run python scripts/eval.py     # rehearsal set: must show 0 wrong verdicts
uv run python scripts/kill_test.py   # 20 random kill -9s: must pass 20/20
```

`MOCK=1` runs offline: GST and parse fall back to fixtures, every fact they produce is tagged mock, and the page shows a MOCK banner. Stage runs use `MOCK=0`.

Without `uv`, a plain venv works: `python -m venv .venv`, then install the dependencies listed in `pyproject.toml` and run the same commands with `.venv/bin/python -m …`.

## How it works

```
request → mask → parse (Haiku, no tools) → formats (c1) → discover → resolve → gather → validate → checks → decide → cards
```

- **Agents read and extract; code decides.** Model output is checked against a schema and never sets a verdict.
- **No source, no claim.** Every fact points at a field path or exact quote in a stored raw response.
- **Mask before the model.** The Aadhaar is dropped. Phone, address, director names and the account number (stored as a hash plus its last 4) are masked before anything is stored.
- **Every step is checkpointed** in SQLite (WAL). Outside calls are keyed by `sha256(provider + request)` and never fetched twice. The worker survives `kill -9`.
- **Rules are data.** Thresholds, providers, prices and limits are in `rules.yaml`; card sentences are in `templates.yaml`. Edit either, then press **Redecide**: the verdict is rerun from stored evidence with no outside calls.
- **Cost and limits.** The cost of every call (GST price, Crustdata credits, Haiku tokens) is recorded, and the job total is shown on the approver card. The cost cap and time limit turn the remaining checks into Cannot verify.

## Where things are

| Path | What |
|---|---|
| `cc/` | `core` (models, config), `store` (SQLite), `intake` (masking, c1, parse), `sources` (call wrapper, providers), `crustdata`, `checks` (validator, checks, `decide`), `output` (the only serializer, cards), `worker`, `app` |
| `web/index.html` | The single page (plain JS, polls the API) |
| `rules.yaml`, `templates.yaml` | Rules and card sentences |
| `tests/cases/` | Rehearsal set, one JSON per case |
| `plan/` | The build plan: [overview](plan/00-overview.md) and one file per phase, with design decisions as built |

## Decisions

The full list is in [overview §3](plan/00-overview.md#3-decisions-settled-dont-reopen-them-without-a-reason). The ones a reviewer will ask about:

- **Name-only input works.** Discovery finds candidate GSTINs and confirms each with GST. Candidates are grouped by PAN: one PAN is decided at PAN level, several PANs give Refer. Lookalikes count only in name-only discovery.
- **Decision order:** Reject, then Refer, then Cannot verify, then Clear. Clear needs c1, c2, c3, c4 and c9; c5 to c7 can only add Refer or Reject.
- **c9 checks the IFSC and the payee name only.** The account holder isn't checked, and the approver card says so.
- **No payment and no sending.** Approval writes a record and nothing else. Follow-up asks are drafted and shown with a copy button, never sent.
- **Namesakes:** 2 or more anchors tie a news passage to our director and give Refer; 1 is a gap; 0 means nothing about that person is stored.
- **Hindi** sentences are drafted in `templates.yaml` and tagged "unreviewed translation" until a Hindi reader checks them. The model translates nothing at run time.
- **One model:** `claude-haiku-5-5` for every model call. The worker is a separate process, so the page survives `kill -9`.

## Latest runs

- `logs/kill_test.txt`: 20/20 kills passed (22 November 2026, `MOCK=1`).
- `logs/eval.txt`: `MOCK=1` with no live keys. Cases 2, 3, 4, 6, 7 and 8 match. Cases 1 and 5 are name-only and need live Crustdata discovery, so without a key they end in Cannot verify. Case 9 is pending a confirmed GSTIN. The `MOCK=0` run on stage is still to do.
