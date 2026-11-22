# Carrier Clearance

An agent that checks a new carrier (truck operator) for an Indian 3PL. Give it a carrier name, a form, or a pasted WhatsApp or email message. It returns a sourced verdict (**Clear**, **Refer**, **Reject** or **Cannot verify**), a vendor card in English and Hindi, and an approver card. Nothing executes after approval: there's no payment, no email and no vendor-master write.

## Run

Everything runs through `uv` with Python 3.12, from `.python-version`:

```sh
cp .env.example .env              # add keys; MOCK=1 uses GST and parse fixtures
uv run uvicorn cc.app:app --reload   # http://127.0.0.1:8000, starts the worker
uv run pytest tests/              # unit tests
uv run python scripts/eval.py     # rehearsal set: must show 0 wrong verdicts
uv run python scripts/kill_test.py   # 20 random kill -9s: must pass 20/20
```

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
