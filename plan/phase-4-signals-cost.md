# Phase 4: Signals, cost and live fixes

**Time:** 1:50–2:30 · **Owners:** A (signals), B (cost and live fixes) · [Overview](00-overview.md)

**Goal:** the optional checks raise real risk signals without blocking a Clear. Every job shows its measured rupee cost. Any rehearsed live fix takes under 5 minutes.

## A: c5, c6, c7

- [x] `cc/crustdata.py`: add the Enrich `web_traffic`, `hiring`, `news` and `people` groups, and Person Search.
- [x] `cc/sources.py`, each adding only a few lines:
  - OpenSanctions lookup.
  - RDAP lookup of the domain's registration date.
  - MCA company master data from data.gov.in, for struck-off status. Optional: skip it if there's no key by 2:00.
- [x] `cc/checks.py`:
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
- [~] Add rehearsal case 9 to `tests/cases/`. It's there, pending a confirmed GSTIN (see wrap-up).

## B: cost, limits, redecide

- [ ] Cost per call in `calls`:
  - Crustdata credits × `prices_inr.crustdata_credit`.
  - The GST per-call price.
  - Haiku input and output tokens × the prices in `rules.yaml`.

  The job total is the sum over that job's calls. It's shown on the approver card and in the output's `cost_inr`.
- [ ] Limits in `cc/worker.py`:
  - Before each paid call, if the job's spend plus the call's price would pass `cost_cap_inr`, the remaining checks get Cannot verify: over cost cap.
  - Past `time_limit_s`, the remaining checks get Cannot verify: over time limit.
- [ ] `redecide(job_id)` in `cc/worker.py`, plus `POST /jobs/{id}/redecide`. It reruns checks and decide from stored evidence with the current `rules.yaml`, makes no outside calls, adds a new `verdicts` row and voids the approval if the hash changed. The page shows the old and new verdicts side by side.
- [ ] Rehearse each live fix once and time it:

  | Likely request | Where | Then |
  |---|---|---|
  | Add an output field, such as registration age in months | `cc/output.py` `serialize()` | redecide |
  | Lower the cost cap | `rules.yaml` | rerun; checks over the cap show Cannot verify |
  | New rule: GST registered under 6 months gives Refer | `rules.yaml`, plus one sentence in each language in `templates.yaml` | redecide |
  | Raise the namesake threshold from 2 anchors to 3 | `rules.yaml` | redecide |

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
- [ ] The rupee total on the approver card equals the sum of the job's `calls` rows.
- [ ] Lowering `cost_cap_inr` turns the remaining checks into Cannot verify: over cost cap.
- [ ] Each live fix above works in under 5 minutes.
- [ ] `scripts/eval.py` shows no wrong verdict, and `scripts/kill_test.py` still passes.

## A wrap-up (phase 4A)

**State:** c5, c6 and c7 are built and wired into `step_checks` after c9. `pytest`: 108 passed; only the 2 phase-0 gates fail (Haiku key, carrier confirmation). Live spend for 4A: 1 credit (the case 9 search). Eval and the kill test are not rerun yet.

**Decisions (changes to the list above):**
- **One Enrich call** serves c3 and c5. Its groups come from `enrich_groups`, now `[basic_info, revenue, news, hiring]`. `people` is out: it would store employee names, and c7 screens only the directors given. `web_traffic` is out: it's keyed by domain (dots), so no field path reaches it.
- **Website live and RDAP are plain free GETs** through `call()` (`site_check`, `rdap`), not Web Fetch. The website is Crustdata's `basic_info.website`.
- **Person Search is not wired.** It can't raise a signal on its own, and it costs credits.
- **OpenSanctions** is wired but there's no `OPENSANCTIONS_API_KEY`, so c7 lists it as a gap. **MCA** is not wired (no key); `struck_off` stays in `hard_fails`, unused.
- **c7 hit:** a Web Search passage (`sources: [news, web]`, `location: IN`, `start_date` = the job's day minus `news_months`, so the call key is stable) that names the director and has an `adverse_terms` word. An OpenSanctions Person result naming the director is a hit with no term needed. Anchors: employer (vendor name without the company suffix), city, DIN, role (`signals.roles`).
- **0 anchors:** the director's call rows are purged in the gather step's transaction: request and response blanked, call key freed (`purged:<id>`), cost kept. The step output holds director indexes and anchor names, never names. Names live only in `jobs.request` (B's masking decides that).
- **c7 counts anchors at gather time, the threshold at check time,** so `anchors_to_refer` changes work on redecide.
- New `rules.yaml` keys: `thresholds.activity_months`, `stale_after_months`, `news_months`; `signals.adverse_terms`, `signals.roles`.

**New rule_ids for B's `templates.yaml`:**
- c5: `active`, `dormant`, `no_recent_activity`, `no_activity_data`
- c6: `age_address_ok`, `gst_too_new`, `state_mismatch`, `domain_too_new`, `gst_date_missing`
- c7: `people_not_given`, `people_clear`, `people_one_anchor`, `people_adverse`
- c5-c7 also: `not_checked`

**Open:**
- **Case 9 GSTIN not findable by discovery.** One live search (1 credit, saved to `logs/probe_crustdata_websearch_case9.json`) for "Rajasthan Logistics Private Limited GST number" returned 10 results: GSTINs of other firms (e.g. Punjab Rajasthan Logistics, 05BIMPP8051P2ZH) and no `08AAFCR1929K…`. So live, case 9 ends Cannot verify (`discovery_none`), which is the honest answer, and c7 never runs because gather needs a GSTIN. No GSTIN was guessed or seeded. Case 9 now `needs: [c7, confirmed_gstin]` and stays pending in eval until the GSTIN is confirmed (GST portal search by PAN AAFCR1929K) and added to its request. c7's namesake behaviour is covered offline by `test_c7_namesake_raises_nothing_and_nothing_is_stored`.
- Live eval and kill test not rerun for 4A (they spend credits). The kill test should switch Crustdata off for its worker first (phase-2 open item).
- Case 2's site rajeshrpl.in answered 200 when checked by hand, so c5 should pass live.
