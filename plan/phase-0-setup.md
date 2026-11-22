# Phase 0: Setup

**Time:** 0:00–0:15 · **Owners:** A and B together · [Overview](00-overview.md)

**Goal:** working keys, a decision on whether phase 1 runs on GST fixtures, and named real carriers for the rehearsal set.

## Tasks

- [x] Create `.env` from `.env.example` (`ANTHROPIC_API_KEY`, `CRUSTDATA_API_KEY`, `GST_PROVIDER`, `GST_CLIENT_ID`, `GST_CLIENT_SECRET`, `OPENSANCTIONS_API_KEY`, `MOCK`). It's created with `MOCK=1`; **the keys are still empty**.
- [ ] Make one Haiku call with `claude-haiku-5-5` to confirm the key and its rate limit.
- [ ] Crustdata:
  - Call the account endpoints and write which endpoints and add-ons the key has into `rules.yaml`. Switch off any rung that isn't on the key.
  - Run Identify and Enrich on 10 real Indian carriers. Note the hit rate and which field groups come back.
- [x] GST (no key yet: running on `MOCK=1` with Cashfree-shaped fixtures in `cc/sources.py`):
  - Try Cashfree's GSTIN API first, then Surepass, then gstinapi.in. Make one live lookup and save the raw response.
  - If none works by 0:15, set `MOCK=1`, build the fixture from that saved response or the provider's documented sample, and keep going. Swap in the live provider as soon as a key works; it's only a `rules.yaml` change.
- [ ] Pick the real carriers for rehearsal cases 1–9 (candidates written to `tests/cases/`, `status: unconfirmed` until checked) ([overview §5](00-overview.md#5-rehearsal-set)). Find:
  - a cancelled GSTIN,
  - a lookalike pair,
  - a proprietorship whose trade name differs from the owner's name,
  - a director with a common name.
- [x] Write `data/vendor_master.csv` with about 10 made-up rows, plus one row matching rehearsal case 2 (it's needed for case 6).
- [ ] Ask the organizers the open questions in [overview §7](00-overview.md#7-open-questions-for-the-organizers).

## Done when

- [ ] Crustdata and Haiku answer live.
- [x] GST answers live, or `MOCK=1` is set with a fixture in place.
- [ ] The 9 rehearsal carriers are named.
- [x] `data/vendor_master.csv` exists.

## How to check

- `uv run pytest`: everything except the `test_phase0_gate_*` tests should pass. Each gate test says what is missing.
- `uv run python -m cc.crustdata`: account endpoints, then Identify and Enrich on 10 carriers (spends credits). Writes `logs/probe_crustdata_*.json` and prints the `rungs` to copy into `rules.yaml`.
- `uv run python -m cc.sources`: the Haiku call and, with a Cashfree key, one live GST lookup. Writes `logs/probe_haiku.json` and `logs/probe_gst_live.json`.
- Confirming a carrier: check it on gst.gov.in (or a live GST lookup), then set `"status": "confirmed"` in its case file.

## Wrap-up (22 November 2026)

**State:** the scaffold and config are done. `uv run pytest` gives 46 passed and 4 failed; the 4 failures are the gate tests, open on purpose. Design decisions are in [overview §3, "Added in phase 0"](00-overview.md#3-decisions-settled-dont-reopen-them-without-a-reason).

**Built:**

| File | What |
|---|---|
| `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore` | Toolchain (outside the §4.6 list; needed for uv and Python 3.12, and to keep `.env` and `logs/` out of git) |
| `.env.example`, `.env` | Key template; local `.env` with `MOCK=1` |
| `rules.yaml` | The §4.7 shape with the real Crustdata groups and the `rungs` block; GST set to `[fixture]` |
| `cc/core.py` | `ROOT`, `LOGS`, `MODEL`, `.env` loading, `mock()`, `load_rules()` |
| `cc/intake.py` | `gstin_ok` (format and checksum, checked on 10 real GSTINs), `pan_ok`, `pan_from_gstin`, `account_hash` |
| `cc/sources.py` | GST fixtures (Cashfree shape: the documented sample, plus cases 2 and 4), `gst_fixture()` (refuses unless `MOCK=1`), Haiku and GST probes |
| `cc/crustdata.py` | `account_endpoints`, `account_credits`, `identify`, `enrich`, the 10-carrier probe, `RUNG_PATHS`, `ENRICH_GROUPS` |
| `data/vendor_master.csv` | 10 made-up vendors (valid checksums, hashed accounts), plus V011 = case 2's carrier |
| `tests/cases/case_01..09.json` | Rehearsal candidates, all `status: unconfirmed` |
| `tests/test_intake.py`, `tests/test_decide.py` | Config tests and the phase-0 gates |

**Carried into phase 1** (each is a red `test_phase0_gate_*`):

- [ ] Put `ANTHROPIC_API_KEY` in `.env`, then run `uv run python -m cc.sources`.
- [ ] Put `CRUSTDATA_API_KEY` in `.env`, then run `uv run python -m cc.crustdata`. Copy the printed `rungs` into `rules.yaml`, and note the hit rate here.
- [ ] Confirm the 9 carriers:
  - Case 4's cancelled status and case 3's owner name need a GST lookup.
  - Case 9's GSTIN hasn't been found yet.
- [ ] GST key (optional): when one works, set `providers.gst: [cashfree]` and `MOCK=0`.
- [ ] Ask the organizers the [§7 questions](00-overview.md#7-open-questions-for-the-organizers).

None of these blocks phase 1, which runs on `MOCK=1`. They have to be closed before the phase 2 live runs.
