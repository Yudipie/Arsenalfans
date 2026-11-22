# Phase 0: Setup

**Time:** 0:00–0:15 · **Owners:** A and B together · [Overview](00-overview.md)

**Goal:** working keys, a decision on whether phase 1 runs on GST fixtures, and named real carriers for the rehearsal set.

## Tasks

- [ ] Create `.env` from `.env.example`: `CRUSTDATA_API_KEY`, `ANTHROPIC_API_KEY`, `GST_PROVIDER_KEY(S)`, `OPENSANCTIONS_API_KEY`, `MOCK=0`.
- [ ] Make one Haiku call with `claude-haiku-5-5` to confirm the key and its rate limit.
- [ ] Crustdata:
  - Call the account endpoints and write which endpoints and add-ons the key has into `rules.yaml`. Switch off any rung that isn't on the key.
  - Run Identify and Enrich on 10 real Indian carriers. Note the hit rate and which field groups come back.
- [ ] GST:
  - Try Cashfree's GSTIN API first, then Surepass, then gstinapi.in. Make one live lookup and save the raw response.
  - If none works by 0:15, set `MOCK=1`, build the fixture from that saved response or the provider's documented sample, and keep going. Swap in the live provider as soon as a key works; it's only a `rules.yaml` change.
- [ ] Pick the real carriers for rehearsal cases 1–9 ([overview §5](00-overview.md#5-rehearsal-set)). Find:
  - a cancelled GSTIN,
  - a lookalike pair,
  - a proprietorship whose trade name differs from the owner's name,
  - a director with a common name.
- [ ] Write `data/vendor_master.csv` with about 10 made-up rows, plus one row matching rehearsal case 2 (it's needed for case 6).
- [ ] Ask the organizers the open questions in [overview §7](00-overview.md#7-open-questions-for-the-organizers).

## Done when

- [ ] Crustdata and Haiku answer live.
- [ ] GST answers live, or `MOCK=1` is set with a fixture in place.
- [ ] The 9 rehearsal carriers are named.
- [ ] `data/vendor_master.csv` exists.
