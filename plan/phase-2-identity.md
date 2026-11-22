# Phase 2: Identity

**Time:** 1:00–1:50 · **Owner:** A (in parallel with phase 3 for B) · [Overview](00-overview.md)

**Goal:** typing only a carrier's name gives a sourced verdict. Lookalikes, duplicates and changed bank details give Refer. A proprietorship resolves to one company.

## Tasks

- [x] `cc/crustdata.py`:
  - Identify, Enrich (`basic_info` and `revenue` groups only, for now), Web Search (`location: IN`) and Web Fetch.
  - Every call goes through `sources.call()`.
  - A 403 switches that rung off for the job, and the check records Cannot verify with the source named.
- [x] `cc/resolve.py`:
  - **Discovery,** when there's no GSTIN:
    1. Run Web Search for `"<name> GSTIN"`, and Crustdata Identify on the name.
    2. Regex the GSTINs out of the stored passages and keep those that pass the c1 format check.
    3. Group them by the embedded PAN and keep at most `max_candidates`.
    4. Make one GST call per PAN, choosing the GSTIN in the stated state if there is one, or else the first active one.
    5. Decide: one PAN continues, decided at PAN level with the other GSTINs listed; several PANs give Refer, showing all of them; none gives Cannot verify, with a drafted ask for the GSTIN.
    - If the GST provider can search by name or PAN, use that as the first rung.
  - **Name normalisation:** drop "M/s", treat "Pvt Ltd" as "Private Limited", "&" as "and", ignore case and punctuation. Identity is confirmed through GST, never by name alone.
  - **Matching:** against the GST legal name or trade name with `fuzzy_cutoff`. When the PAN's fourth letter is `P` (a proprietorship), match on the trade name and treat the owner's legal name as personal data.
  - **Lookalikes:** Crustdata or search hits with near-identical names but different GSTIN, PAN or city become candidates for c3. They give Refer only in name-only discovery. On a full request whose GSTIN is GST-confirmed, they're listed on the approver card and nothing more (overview §3).
- [x] `cc/sources.py`: IFSC lookup through the free public IFSC API, which returns bank and branch.
- [x] `cc/checks.py`:
  - **c3:** one GST-confirmed identity passes. A tie or several PANs give Refer, listing both candidates and their sources. `revenue.acquisition_status` showing acquired or merged gives Refer.
  - **c4:** reads `vendor_master.csv` fresh on every job, and looks up earlier jobs by GSTIN, PAN and bank hash.
    - Same GSTIN or PAN under another name gives Refer, citing the row.
    - Same PAN in another state is a branch and passes.
    - Same GSTIN with a new bank hash gives Refer, citing the earlier job.
  - **c9:** an IFSC that isn't found gives Cannot verify and an ask to the vendor. A payee name that matches neither the GST legal nor the trade name gives Refer. The card adds: "Account holder not verified."
- [x] `web/index.html`: candidates side by side, each with its source.
- [x] Add rehearsal cases 1, 3, 5, 6 and 7 to `tests/cases/`.

## Edge cases closed

| ID | Case | Result |
|---|---|---|
| E9 | Name variants (M/s, Pvt Ltd, &) | Normalised before matching; confirmed through GST |
| E10 | Proprietorship | Matched on the trade name; the owner's name is personal data |
| E11, E12 | Lookalike, or an Identify tie | Refer, with both candidates |
| E13 | Not in Crustdata | GST carries c2, c3 and c9; Cannot verify only where no rung finds the fact |
| E16 | Acquired or merged | Refer |
| E18 | Same company under two names | Refer, citing the vendor-master row |
| E19 | Same PAN in another state | A branch, not a duplicate |
| E20 | Same GSTIN with a new bank account | Refer, citing the earlier job |
| E22 | IFSC not found | Cannot verify; ask the vendor |
| E23 | Payee name differs from the GST legal and trade names | Refer |
| E37 | Vendor master edited mid-build | Read fresh every job and never cached |

## Done when

- [~] Case 1 (name only, a large carrier): Clear, decided at PAN level, with the other GSTINs listed.
- [x] Case 3 (proprietorship): Clear on the trade name.
- [~] Case 5 (lookalike): Refer, with both candidates side by side.
- [x] Cases 6 and 7: Refer, citing the vendor-master row and the earlier job.
- [ ] `scripts/eval.py` shows no wrong verdict, and `scripts/kill_test.py` still passes.

## Wrap-up (22 November 2026)

**State:** c3, c4 and c9 are built, and so is name-only discovery. No check reports `not_built` any more.

- **Tests:** `uv run pytest` gives 95 passed and 4 failed. The 4 failures are the phase-0 gates, still open. The phase-2 tests run offline: the `db` fixture stubs Crustdata and IFSC, so pytest spends no credits.
- **Eval, live** (Crustdata on, GST fixtures, IFSC live): **0 wrong.** Cases 1–8 are ok and case 9 is pending (needs c7). Every job takes under 20 s.
- **Probe:** `python -m cc.crustdata` gives a 100% Identify hit rate on the 10 carriers, all rungs enabled, 22 credits.
- **Kill test, live: 10/20.** All 20 verdicts matched the uninterrupted run. All 10 failures are a Crustdata **429** on the call repeated after the kill: 40 back-to-back jobs on fresh databases hit the rate limit. The call ended as `error` and the job continued without Identify/Enrich, which is optional, so the verdict was unchanged. Resume and checkpointing are fine; the test is strict about call counts. It cost about 31 credits. **Don't rerun it live:** it repeats every Crustdata call on every round.
- **Credits:** 143.3 left after phase 2. 140 of them expire on 6 December.

**`[~]` means live search, but GST still from fixtures (MOCK).**
- Cases 1 and 5 pass in `tests/test_decide.py` when the search returns the GSTINs in their `evidence_note`s.
- On a live run, the Web Search has to actually return those GSTINs. If it returns others, seed them into `GST_FIXTURES` (MOCK) or set `MOCK=0` with a GST key.

**Decisions made in this phase:**

- **No bank details at all:** c9 returns `pass` with `bank_not_given` and a gap ("check before first payment"), so name-only requests can still be Clear.
  - `decide()` now lists any result that carries a gap, but only `cannot_verify` blocks.
  - When any bank detail is given, c9 is required as written.
- **Discovery means "no GSTIN given",** whatever `received_via` says (case 5 is a `form`).
- **Discovery is decided at PAN level.** Per PAN, GST is tried in order (the stated state first) until a GSTIN is Active, up to `thresholds.gst_tries_per_pan` (2). So a cancelled sibling GSTIN never Rejects.
  - A PAN is kept only if its GST name matches the requested name, which drops unrelated GSTINs on the same search page.
  - Several matching PANs, or an Identify tie, give Refer (`several_pans`, `identify_tie`).
- **Candidate facts are `cand.<GSTIN>.*`.** The resolved GSTIN keeps `gst.*`, so c2 reads only that GSTIN. GSTINs found in search are quote-backed `search.<GSTIN>` facts. Case 5's `expect_facts` is now its two `cand.*.trade_name` facts.
- **c1 after discovery:** `step_checks` re-runs `check_formats` with the discovered GSTIN when c1 was `gstin_missing`.
- **Free sources go through `call()` so their facts have a stored response:**
  - `vendor_master`, keyed by the CSV's sha256 (an edited file is a new snapshot, E37);
  - `earlier_jobs`, keyed by `job_id`, which returns other decided jobs with the same GSTIN or PAN;
  - `razorpay_ifsc`, where a 404 is `{"found": false}` and not retried.
- **Crustdata providers** (`crustdata.PROVIDERS`):
  - They return `{"body", "credits"}`. `call()` prices them as `credits × prices_inr.crustdata_credit`, which is still 0.0 until the plan price is known.
  - A 403 raises `RungOff`, which is not retried. The rung is noted off for the job and is never blocking, because GST carries c3 (E13).
  - Web Search is `POST /web/search/live` with `{query, sources: [web], location: IN}`. Web Fetch is `POST /web/enrich/live` with `{urls}`, and runs only when no search snippet holds a GSTIN.
- **`get_path` indexes lists** (`body.0.matches.1.…`), for Crustdata's array responses.
- **`jobs.pan` is filled from the GSTIN** when no PAN was given, so earlier-job lookups by PAN work.
- **Candidates on the page** are built from c3's cited `cand.*` facts and the `search.*` facts in `web/index.html`. `output.py` and `app.py` (B's files) weren't touched.
- **Eval `needs`:** a case file can list checks it depends on (case 9: `["c7"]`). The case is pending until those exist. Without `needs`, every mismatch counts as wrong.

**New rule_ids (B: `templates.yaml` needs a sentence for each):**
- c3: `identity_confirmed`, `identity_pan_level`, `identity_unconfirmed`, `name_mismatch`, `several_pans`, `identify_tie`, `acquired_or_merged`, `discovery_none`, `not_checked`
- c4: `dup_none`, `dup_known_vendor`, `dup_branch`, `dup_other_name`, `bank_changed`, `not_checked`
- c9: `bank_ok`, `bank_not_given`, `ifsc_missing`, `ifsc_not_found`, `payee_missing`, `payee_mismatch`, `source_unavailable`, `not_checked`

**Left open:**
- [x] Run `uv run python -m cc.crustdata`. The rungs were already all `true`.
- [x] Live eval for cases 1 and 5: 0 wrong. The query is now `"<name> GST number"`, and the GSTINs it returned are seeded into `GST_FIXTURES`. Their status is unverified.
- [ ] `scripts/kill_test.py` 20/20. Proposed fix: the kill test switches Crustdata off for its worker, using a test hook like `CC_STEP_DELAY`. It tests our checkpointing, not Crustdata, and GST fixture calls already cover the in-flight case. That run is free.
- [ ] Set `prices_inr.crustdata_credit` from the plan, so the job cost includes Crustdata.
- [ ] Check the VRL fixture's district (Dharwad) against a live GST lookup.

## Notes for phase 4

- `resolve.crustdata_identity()` already calls Identify and Enrich (`basic_info`, `revenue`) on the best name match. c5 can add `web_traffic`, `hiring` and `news` to the same Enrich call and read the stored response.
- `resolve._guarded()` is the pattern for an optional Crustdata rung: off, 403 or unavailable becomes a note and never blocks.
- Case 9 has `needs: ["c7"]` and no GSTIN, so it goes through discovery. Its GSTIN isn't in `GST_FIXTURES` yet.
