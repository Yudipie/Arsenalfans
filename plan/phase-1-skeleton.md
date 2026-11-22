# Phase 1: Walking skeleton

**Time:** 0:15–1:00 · **Owners:** A and B in parallel · [Overview](00-overview.md)

**Goal:** a real GSTIN typed into the page comes out as a sourced verdict. Approving records an approval bound to the verdict hash. Killing the worker at any moment and restarting it gives the same verdict, without calling the GST API again.

## A: store, worker, sources

- [x] `cc/store.py`: SQLite in WAL mode with the 6 tables ([overview §4.5](00-overview.md#45-sqlite-tables-ccstorepy)), one transaction per step.
- [x] `cc/worker.py`:
  - Polls `jobs` for work and runs the steps in order: intake, formats, resolve, gather, checks, decide, output. Each step's output goes to `steps` before the next step starts.
  - On start, it resumes any job that isn't finished from its last finished step.
  - Time-limit and cost-cap stubs; phase 4 completes them.
- [x] `cc/sources.py`:
  - The `call()` wrapper:
    1. Compute the call key as `sha256(provider + request)`.
    2. If `calls` already holds a response for that key, return it.
    3. Otherwise call with a timeout and 1 retry, then try the 1 fallback provider.
    4. Write the `calls` row with the raw response and its cost.
  - The GST provider adapter(s), normalised to: status, legal name, trade name, address, state, registration date, last updated.
  - The `fixture` provider, which refuses to run unless `MOCK=1` and marks every fact `mock: true`.
  - `read_vendor_master()`, which reads the CSV fresh on every job.
- [x] `scripts/kill_test.py`: 20 rounds. Each round submits a job, sends `kill -9` to the worker after a random delay, restarts it, and waits for the verdict. It asserts:
  - the verdict matches an uninterrupted run;
  - no call key has more than one completed response, apart from a call in flight at the kill;
  - approving twice gives one approval row.

  Output is saved to `logs/kill_test.txt`.

## B: core, checks, output, app, page

- [x] `cc/core.py`: pydantic models for the request, the onboarding record, evidence, check result, verdict and approval; a loader for `rules.yaml`, `templates.yaml` and `.env`; the rules hash. *(Done without the `templates.yaml` loader, which comes in phase 3, and without the Verdict and Approval models, which would go unused; see overview §3.)*
- [x] `rules.yaml`: a first version in the shape shown in [overview §4.7](00-overview.md#47-rulesyaml-shape).
- [x] `cc/intake.py`, format part only: form fields map straight to the record, with no model yet.
  - GSTIN length, check digit, state code, embedded PAN and PAN holder type.
  - IFSC format.
  - PAN given against the PAN inside the GSTIN.
  - O-for-0 noted in `parse_notes` and never fixed.
- [x] `cc/checks.py`:
  - The validator, which drops any fact whose `field_path` or `quote` isn't in the stored response.
  - c1 and c2.
  - `decide()` in the order Reject → Refer → Cannot verify → Clear, reading `required_checks` from `rules.yaml`.
- [x] `cc/output.py`: `serialize()` and a plain approver card; `approval_hash()`.
- [x] `cc/app.py` (FastAPI). It starts `worker.py` as a subprocess and keeps its PID. Endpoints:

  | Endpoint | What it does |
  |---|---|
  | `POST /jobs` | Submit a request |
  | `GET /jobs/{id}` | Steps, checks, evidence, verdict, approval, cost |
  | `POST /jobs/{id}/approve` | Takes the hash the page shows, recomputes it from current state, and returns 409 if they differ |
  | `POST /jobs/{id}/edit` | Changes a field and reruns from the formats step, which voids any approval |
  | `POST /worker/kill` | Sends `kill -9` to the worker |
  | `POST /worker/restart` | Starts the worker again |

- [x] `web/index.html`, polling every 500 ms:
  - **Top:** input box, with a sample-request dropdown that loads the `tests/cases/` requests (`GET /samples`).
  - **Left:** checks lighting up.
  - **Middle:** the stored response with the deciding field highlighted, its date and its cost.
  - **Right:** verdict, approver card and the approve button.
  - **Bottom strip:** worker status, kill and restart buttons, and the resume line ("resumed at `<step>` · N paid calls repeated · ₹X re-spent").
  - A MOCK banner when `MOCK=1`.

## Tests

- [x] `tests/test_intake.py`: format cases (14 characters, a bad check digit, a wrong state code, an O for a 0, a PAN mismatch).
- [x] `tests/test_decide.py`: the decision order, including Refer beating Cannot verify.
- [x] `tests/cases/02_*.json` and `tests/cases/04_*.json`; `scripts/eval.py` runs every file in `tests/cases/`.

## Edge cases closed

| ID | Case | Result |
|---|---|---|
| E3 | Malformed GSTIN (wrong length, O for 0, bad check digit) | Cannot verify: fix the field. No paid call. The O for 0 is noted and never corrected |
| E4 (note) | GSTIN state code doesn't match the stated state | Noted at intake; compared fully in phase 4 |
| E5 | PAN given differs from the PAN inside the GSTIN | Cannot verify: ask the vendor which one is right |
| E14 | GST cancelled or suspended | Reject, citing the status field and its date |
| E24 | Details edited after approval | Approval void; back to awaiting approval under a new hash |
| E31 | Source times out or errors | 1 retry, then the fallback, then Cannot verify: source unavailable |
| E32 | A quote or field path that isn't in the stored response | Fact dropped by the validator |
| E40 | Kill during evidence gathering | Resume at that step; stored responses are reused |
| E41 | Kill while awaiting approval | Resume to awaiting approval |
| E44 | Kill during a SQLite write | WAL plus one transaction per step; the half-written step runs again |

## Done when

- [~] A real active GSTIN gives Clear on c1 and c2, and clicking the GST status shows the highlighted field and its date.
- [~] A real cancelled GSTIN gives Reject.
- [x] Approve creates one approval row. Editing the bank account voids it.
- [x] `scripts/kill_test.py` passes 20 out of 20.

## Wrap-up (22 November 2026)

**State:** the skeleton works end to end on `MOCK=1`. The phase-1 decisions are in [overview §3, "Added in phase 1"](00-overview.md#3-decisions-settled-dont-reopen-them-without-a-reason).

- **Tests:** `uv run pytest` gives 72 passed and 4 failed. The 4 failures are the phase-0 gates, still open.
- **Eval:** `scripts/eval.py` shows 0 wrong. Cases 4 and 8 are ok; the other seven are pending on c3, c4 and c9.
- **Kill test:** `scripts/kill_test.py` passes 20/20. When a kill lands during the GST call, that call repeats once and is counted.

**Run it:** `uv run uvicorn cc.app:app --reload`, then open http://127.0.0.1:8000.

**`[~]` means done on fixtures, not live.** Cases 2 and 4 come from the GST fixture, because there's no GST key yet. They become real once `providers.gst: [cashfree]` and `MOCK=0` are set.

**Not checked by eye:** the page's highlight of the deciding field. The API side was exercised with curl:
- submit;
- approve twice, which leaves 1 row;
- a stale hash, which gets 409;
- an account edit, which voids the approval and gives a new hash;
- kill and restart.

**Left open (none of it blocks phase 2 or 3):**
- The two `[~]` items need a live GST key: set `providers.gst: [cashfree]` and `MOCK=0`, then rerun cases 2 and 4.
- Someone has to open the page and click `gst.status` to check that the field is highlighted.

**Phase 1 is closed.**

## Notes for phases 2 and 3

What the phase-1 code already does, and the traps found while building it.

### Both phases

- **Every outside call, Haiku included, goes through `sources.call(job_id, providers, request, fns)`.**
  - `request` is a dict passed as keyword arguments to `fns[provider]`.
  - It returns `(calls row, response)`.
  - Use the row's `call_id` and `finished_at` on facts; `gst_lookup()` shows the pattern.
- **A step is `(job, done) -> (output, extra)`.** `extra(conn)` runs inside the step's transaction; put evidence and other side writes there and nowhere else.
  - Outside calls commit on their own, before the step does. That's why a kill never fetches a stored call again.
- **To add a check,** replace its `checks.not_built(...)` line in `worker.step_checks`.
  - The eval marks a case pending only while some check is `not_built`. When the last one is gone, every mismatch counts as wrong.
- **A new `rule_id` needs a sentence in `templates.yaml`** (phase 3). The ones so far:
  - c1: `formats_ok`, `gstin_missing`, `gstin_format`, `gstin_state_code`, `pan_mismatch`, `ifsc_format`.
  - c2: `gst_active`, `gst_cancelled`, `gst_suspended`, `gst_not_found`, `gst_status_other`, `source_unavailable`, `not_checked`.
  - Placeholder: `not_built`.
- **Eval can only find a fact the code emits under that exact `fact_id`.** The case files expect `ifsc.bank` and `vendor_master.V011`.

### Phase 2 (identity)

- **Every new error is retried.** `call()` retries any exception once, then moves to the next provider. A Crustdata 403 ("rung off") would be retried pointlessly. Raise a distinct exception for it and let it through without the retry.
- **Crustdata cost comes from a header that `call()` never sees.** Today the cost is `prices_inr[PRICE_KEYS[provider]]`, a flat price, and `call()` never sees response headers, so the `X-Credits-Used` credit count is lost. Have the provider function return the credits with the body, for example `{"body": …, "credits": n}`. Then have `call()` price them with `crustdata_credit`.
- **The vendor master must be read fresh, but the call cache is keyed by request.** A vendor-master "call" with the same request would reuse an old snapshot across jobs. Put `job_id`, or the CSV's sha256, into its request, so each job stores its own snapshot.
  - Facts need a stored response for the validator, so route `read_vendor_master()` through `call()` as provider `vendor_master`. Treat it as free.
- **Earlier jobs for c4 and case 7** are in `jobs` (the `gstin`, `pan` and `bank_hash` columns, filled at intake). Leave out the current `job_id`.
- **The lookalike rule needs to know if the request is name-only,** but `received_via` isn't in the record. Read it from `json.loads(job["request"])["received_via"]`, or add it to `OnboardingRecord`.
- **Quote-backed facts from web passages:** the validator searches for the quote in `json.dumps(raw)`. A quote with a newline or a `"` is escaped in that string and won't be found. Before Web Search facts arrive, match against the passage's text field instead.
- **`step_resolve` hands `gather` a list of GSTINs** (`{"gstins": [...]}`). Discovery should keep that shape, so `gather` and c2 work unchanged for several candidates.
- **IFSC (c9)** can follow the `gst_lookup` pattern with provider `razorpay_ifsc` (`rules.yaml providers.ifsc`). It's free, so it has no `PRICE_KEYS` entry.

### Phase 3 (intake and cards)

- **`scrub_request()` in `cc/intake.py` is where masking goes.** It runs before the request is stored.
  - The Aadhaar check is a bare regex for now; switch it to Verhoeff.
  - **Director names and the contact email are still stored raw in `jobs.request`.** The record already shows directors as `[personal]`. Decide what c7 needs before masking them at the door.
- **The Haiku parse should run as part of the intake step and go through `call()`.** `calls` already has `tokens_in`, `tokens_out` and `request_id` columns. A kill mid-parse then reuses the stored parse.
  - `record_from_request()` adds the note "pasted text not parsed yet" for text-only requests; replace it.
- **The record already has places for the parse output:** `flagged_lines` (shown on the card and in the output), `parse_notes` (formats notes are merged in at the output step) and `language`.
- **Cards and sentences:**
  - `output.serialize()` sets `text_en: None` on every reason; fill it from `templates.yaml` there.
  - `approver_card()` returns structured data; add `vendor_card()` beside it.
  - The page renders both from `verdict.card`.
- **The page's input box takes JSON only.** Phase 3 needs a plain-text paste box that wraps the text as `{"received_via": "whatsapp", "text": …}`.
