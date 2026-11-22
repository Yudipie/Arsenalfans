# Phase 1: Walking skeleton

**Time:** 0:15–1:00 · **Owners:** A and B in parallel · [Overview](00-overview.md)

**Goal:** a real GSTIN typed into the page comes out as a sourced verdict. Approving records an approval bound to the verdict hash. Killing the worker at any moment and restarting it gives the same verdict, without calling the GST API again.

## A: store, worker, sources

- [ ] `cc/store.py`: SQLite in WAL mode with the 6 tables ([overview §4.5](00-overview.md#45-sqlite-tables-ccstorepy)), one transaction per step.
- [ ] `cc/worker.py`:
  - Polls `jobs` for work and runs the steps in order: intake, formats, resolve, gather, checks, decide, output. Each step's output goes to `steps` before the next step starts.
  - On start, it resumes any job that isn't finished from its last finished step.
  - Time-limit and cost-cap stubs; phase 4 completes them.
- [ ] `cc/sources.py`:
  - The `call()` wrapper:
    1. Compute the call key as `sha256(provider + request)`.
    2. If `calls` already holds a response for that key, return it.
    3. Otherwise call with a timeout and 1 retry, then try the 1 fallback provider.
    4. Write the `calls` row with the raw response and its cost.
  - The GST provider adapter(s), normalised to: status, legal name, trade name, address, state, registration date, last updated.
  - The `fixture` provider, which refuses to run unless `MOCK=1` and marks every fact `mock: true`.
  - `read_vendor_master()`, which reads the CSV fresh on every job.
- [ ] `scripts/kill_test.py`: 20 rounds. Each round submits a job, sends `kill -9` to the worker after a random delay, restarts it, and waits for the verdict. It asserts:
  - the verdict matches an uninterrupted run;
  - no call key has more than one completed response, apart from a call in flight at the kill;
  - approving twice gives one approval row.

  Output is saved to `logs/kill_test.txt`.

## B: core, checks, output, app, page

- [ ] `cc/core.py`: pydantic models for the request, the onboarding record, evidence, check result, verdict and approval; a loader for `rules.yaml`, `templates.yaml` and `.env`; the rules hash.
- [ ] `rules.yaml`: a first version in the shape shown in [overview §4.7](00-overview.md#47-rulesyaml-shape).
- [ ] `cc/intake.py`, format part only: form fields map straight to the record, with no model yet.
  - GSTIN length, check digit, state code, embedded PAN and PAN holder type.
  - IFSC format.
  - PAN given against the PAN inside the GSTIN.
  - O-for-0 noted in `parse_notes` and never fixed.
- [ ] `cc/checks.py`:
  - The validator, which drops any fact whose `field_path` or `quote` isn't in the stored response.
  - c1 and c2.
  - `decide()` in the order Reject → Refer → Cannot verify → Clear, reading `required_checks` from `rules.yaml`.
- [ ] `cc/output.py`: `serialize()` and a plain approver card; `approval_hash()`.
- [ ] `cc/app.py` (FastAPI). It starts `worker.py` as a subprocess and keeps its PID. Endpoints:

  | Endpoint | What it does |
  |---|---|
  | `POST /jobs` | Submit a request |
  | `GET /jobs/{id}` | Steps, checks, evidence, verdict, approval, cost |
  | `POST /jobs/{id}/approve` | Takes the hash the page shows, recomputes it from current state, and returns 409 if they differ |
  | `POST /jobs/{id}/edit` | Changes a field and reruns from the formats step, which voids any approval |
  | `POST /worker/kill` | Sends `kill -9` to the worker |
  | `POST /worker/restart` | Starts the worker again |

- [ ] `web/index.html`, polling every 500 ms:
  - **Top:** input box, with a sample-request dropdown that loads from `data/requests/`.
  - **Left:** checks lighting up.
  - **Middle:** the stored response with the deciding field highlighted, its date and its cost.
  - **Right:** verdict, approver card and the approve button.
  - **Bottom strip:** worker status, kill and restart buttons, and the resume line ("resumed at `<step>` · N paid calls repeated · ₹X re-spent").
  - A MOCK banner when `MOCK=1`.

## Tests

- [ ] `tests/test_intake.py`: format cases (14 characters, a bad check digit, a wrong state code, an O for a 0, a PAN mismatch).
- [ ] `tests/test_decide.py`: the decision order, including Refer beating Cannot verify.
- [ ] `tests/cases/02_*.json` and `tests/cases/04_*.json`; `scripts/eval.py` runs every file in `tests/cases/`.

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

- [ ] A real active GSTIN gives Clear on c1 and c2, and clicking the GST status shows the highlighted field and its date.
- [ ] A real cancelled GSTIN gives Reject.
- [ ] Approve creates one approval row. Editing the bank account voids it.
- [ ] `scripts/kill_test.py` passes 20 out of 20.
