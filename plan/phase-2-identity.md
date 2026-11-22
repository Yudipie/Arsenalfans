# Phase 2: Identity

**Time:** 1:00–1:50 · **Owner:** A (in parallel with phase 3 for B) · [Overview](00-overview.md)

**Goal:** typing only a carrier's name gives a sourced verdict. Lookalikes, duplicates and changed bank details give Refer. A proprietorship resolves to one company.

## Tasks

- [ ] `cc/crustdata.py`:
  - Identify, Enrich (`basic_info` and `revenue` groups only, for now), Web Search (`location: IN`) and Web Fetch.
  - Every call goes through `sources.call()`.
  - A 403 switches that rung off for the job, and the check records Cannot verify with the source named.
- [ ] `cc/resolve.py`:
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
- [ ] `cc/sources.py`: IFSC lookup through the free public IFSC API, which returns bank and branch.
- [ ] `cc/checks.py`:
  - **c3:** one GST-confirmed identity passes. A tie or several PANs give Refer, listing both candidates and their sources. `revenue.acquisition_status` showing acquired or merged gives Refer.
  - **c4:** reads `vendor_master.csv` fresh on every job, and looks up earlier jobs by GSTIN, PAN and bank hash.
    - Same GSTIN or PAN under another name gives Refer, citing the row.
    - Same PAN in another state is a branch and passes.
    - Same GSTIN with a new bank hash gives Refer, citing the earlier job.
  - **c9:** an IFSC that isn't found gives Cannot verify and an ask to the vendor. A payee name that matches neither the GST legal nor the trade name gives Refer. The card adds: "Account holder not verified."
- [ ] `web/index.html`: candidates side by side, each with its source.
- [ ] Add rehearsal cases 1, 3, 5, 6 and 7 to `tests/cases/`.

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

- [ ] Case 1 (name only, a large carrier): Clear, decided at PAN level, with the other GSTINs listed.
- [ ] Case 3 (proprietorship): Clear on the trade name.
- [ ] Case 5 (lookalike): Refer, with both candidates side by side.
- [ ] Cases 6 and 7: Refer, citing the vendor-master row and the earlier job.
- [ ] `scripts/eval.py` shows no wrong verdict, and `scripts/kill_test.py` still passes.
