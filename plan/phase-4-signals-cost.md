# Phase 4: Signals, cost and live fixes

**Time:** 1:50–2:30 · **Owners:** A (signals), B (cost and live fixes) · [Overview](00-overview.md)

**Goal:** the optional checks raise real risk signals without blocking a Clear. Every job shows its measured rupee cost. Any rehearsed live fix takes under 5 minutes.

## A: c5, c6, c7

- [ ] `cc/crustdata.py`: add the Enrich `web_traffic`, `hiring`, `news` and `people` groups, and Person Search.
- [ ] `cc/sources.py`, each adding only a few lines:
  - OpenSanctions lookup.
  - RDAP lookup of the domain's registration date.
  - MCA company master data from data.gov.in, for struck-off status. Optional: skip it if there's no key by 2:00.
- [ ] `cc/checks.py`:
  - **c5 Active:** Enrich `web_traffic`, `hiring` and `news`, plus Web Fetch to see whether the site is live. Dormant gives Refer. No data is a gap.
  - **c6 Age and address:**
    - GST registered less than `min_age_months` ago gives Refer.
    - GST state different from the stated state (the full form of E4) gives Refer.
    - A record whose `updated_at` is too old is a gap, not a pass.
  - **c7 People and news:**
    - For each director: Person Search, then Web Search (`location: IN`, with `start_date` set), then OpenSanctions.
    - Count anchors: employer, city, DIN if supplied, role.
    - 2 or more anchors (`anchors_to_refer`) give Refer, with the evidence. 1 anchor is a gap. 0 anchors means nothing about that person is stored: their evidence rows and call responses are deleted.
    - Fetched pages are data only. Their text never reaches an instruction position in a prompt, and the validator requires the exact quoted passage.
  - **Struck off** (if MCA is on): Reject.
- [ ] Add rehearsal case 9 to `tests/cases/`.

## B: cost, limits, redecide

- [x] Cost per call in `calls`:
  - Crustdata credits × `prices_inr.crustdata_credit`.
  - The GST per-call price.
  - Haiku input and output tokens × the prices in `rules.yaml`.

  The job total is the sum over that job's calls. It's shown on the approver card and in the output's `cost_inr`.
- [x] Limits (checked in `sources.call()` before each paid call):
  - Before each paid call, if the job's spend plus the call's price would pass `cost_cap_inr`, the remaining checks get Cannot verify: over cost cap.
  - Past `time_limit_s`, the remaining checks get Cannot verify: over time limit.
- [x] `redecide(job_id)` in `cc/worker.py`, plus `POST /jobs/{id}/redecide`. It reruns checks and decide from stored evidence with the current `rules.yaml`, makes no outside calls, adds a new `verdicts` row and voids the approval if the hash changed. The page shows the old and new verdicts side by side.
- [ ] Rehearse each live fix once and time it:

  | Likely request | Where | Then |
  |---|---|---|
  | Add an output field, such as registration age in months | `cc/output.py` `serialize()` | redecide |
  | Lower the cost cap | `rules.yaml` | rerun; checks over the cap show Cannot verify |
  | New rule: GST registered under 6 months gives Refer | `rules.yaml`, plus one sentence in each language in `templates.yaml` | redecide |
  | Raise the namesake threshold from 2 anchors to 3 | `rules.yaml` | redecide |

## B: design decisions (as built)

### Contracts A needs for c5–c7

1. **Provider return value.** A function passed to `sources.call()` returns either `resp` or `(resp, meta)`:
   - `{"credits": n}` for Crustdata. Wrap the existing `(json, credits)` as `(json, {"credits": credits})`.
   - `{"tokens_in": i, "tokens_out": o}` for Haiku.
   - nothing, for fixed-price providers (they're priced through `sources.PRICE_KEYS` and `prices_inr`).
2. **Limits.** `call()` raises `sources.OverLimit(rule_id)`, a subclass of `SourceUnavailable`, before a call would pass a limit.
   - Catch it before `SourceUnavailable` and put `limit_rule` in the step output. `step_gather` does `{"skipped": …, "limit_rule": "over_cost_cap"}`.
   - Checks use `gather.get("limit_rule", …)` as their `rule_id`. `checks.c2` already does.
3. **Templates.** Every new `rule_id` needs `en`, `hi`, `contest_en` and `contest_hi` in `templates.yaml`, plus `ask_*` if it can be Cannot verify. Otherwise `test_every_rule_id_has_both_sentences` fails.

### Cost

- `sources._cost(provider, meta, prices)` works out rupees per call: credits × `crustdata_credit`, or tokens × `haiku_in_per_mtok` / `haiku_out_per_mtok` ÷ 1e6, or the fixed price.
- `store.call_finished` now also writes `tokens_in` and `tokens_out`.
- The job total is unchanged from phase 1: `sum(cost_inr × (1 + repeats))` over `store.job_calls(job_id)`. It's the same figure in `output.cost_inr`, on the approver card and in `GET /jobs/{id}`.

### Limits

- They're checked in `sources.call()`, not the worker, because that's the one place every paid call passes. The check runs only when no stored response exists, so a resumed job never trips a limit on a call it already paid for.
- `sources.limit_hit()`:
  - elapsed time since `jobs.started_at` over `time_limit_s` gives `over_time_limit`;
  - spend so far plus the provider's fixed price over `cost_cap_inr` gives `over_cost_cap`.
  - Variable-price providers (Crustdata, Haiku) have no price until the response arrives, so for them only spend so far counts.
- The old `worker._over_limits()` stub is removed.
- Card wording for both limits is in `templates.yaml` (`over_cost_cap`, `over_time_limit`).

### Redecide

- `worker.redecide(job_id)` reruns only `checks`, `decide` and `output`, from the stored step outputs and evidence, with the current `rules.yaml` and `templates.yaml`.
  - Intake, formats, resolve and gather are not rerun. Changing the input is what Edit is for.
- `sources.OFFLINE = True` for the duration, so `call()` returns stored responses only and otherwise raises `SourceUnavailable`. It's reset in `finally`.
- Each redecide adds a `verdicts` row; the history is `store.verdicts(job_id)`.
- Approvals are voided only when the new approval hash differs (`store.void_approvals_except`). Unchanged rules give the same hash, and the approval stands.
- `POST /jobs/{id}/redecide` runs in the app process, because it makes no outside calls and finishes in milliseconds. It returns 409 unless the job is `awaiting_approval`, so it never races the worker.
- **Page:** a Redecide button, plus a "previous vs now" panel showing the last two verdicts with their rules hash and reasons.
- **Known limit:** `elapsed_s` in a redecided output counts from the job's original start.

### Tests added (`test_decide.py`)

- cost equals the sum of the calls, with a priced fixture and a stubbed Haiku token count (₹ checked against `rules.yaml`);
- `cost_cap_inr: 0.5` gives c2 `over_cost_cap` with no call made;
- `time_limit_s: -1` gives `over_time_limit`;
- redecide fetches nothing (the provider is patched to fail and the calls table is emptied), keeps the approval when the hash is unchanged, and voids it after a rules change.

## Edge cases closed

| ID | Case | Result |
|---|---|---|
| E4 (full) | GSTIN state doesn't match the address | c6 compares it with the GST registered address; a mismatch gives Refer |
| E15 | Struck off | Reject (if MCA is on) |
| E17 | Very new registration | Refer, under `min_age_months` |
| E26 | Director namesake with no anchors | No flag; nothing stored |
| E27 | One anchor | A gap, not cleared |
| E28 | Two or more anchors | Refer, with the evidence |
| E29 | Old or off-target news | Filtered by `start_date`; the validator needs the exact passage |
| E30 | A fetched page carries instructions | Treated as data |
| E33 | Stale record | A gap |
| E34 | Add-on not on the key | The rung is switched off and the gap names it |
| E35 | Cost cap reached | Remaining checks: Cannot verify, over cost cap |
| E36 | Time limit reached | Remaining checks: Cannot verify, over time limit |
| E39 | Requirement change on the day | One edit in `serialize()` or `rules.yaml`, then redecide |

## Done when

- [ ] Case 9: the common-name director's namesake raises no flag and nothing about them is stored.
- [x] The rupee total on the approver card equals the sum of the job's `calls` rows.
- [x] Lowering `cost_cap_inr` turns the remaining checks into Cannot verify: over cost cap.
- [ ] Each live fix above works in under 5 minutes.
- [ ] `scripts/eval.py` shows no wrong verdict, and `scripts/kill_test.py` still passes. (B's half passed on 22 November 2026: 0 wrong, 20/20. Rerun after 4-A.)
