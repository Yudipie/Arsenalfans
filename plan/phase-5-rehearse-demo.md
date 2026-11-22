# Phase 5: Rehearse and demo

**Time:** 2:30–3:00 · **Owners:** A and B together · [Overview](00-overview.md)

**Feature freeze at 2:30.** Only fixes from here on.

## Tasks

- [ ] Run `scripts/eval.py` on all 9 cases with `MOCK=0` and save the output to `logs/eval.txt`. Fix any wrong verdict before anything else.
- [ ] Run `scripts/kill_test.py` 20 times and save the output to `logs/kill_test.txt`.
- [ ] Finance: take the median and worst rupees per job from the eval run and write them into the README.
- [ ] Finish `README.md`:
  - the job and who pays for it today;
  - the input and the output;
  - the pass mark;
  - every data source called, and what each one costs;
  - how to run: `.env`, `uvicorn cc.app:app`, `MOCK=1` for offline;
  - the decisions in [overview §3](00-overview.md#3-decisions-settled-dont-reopen-them-without-a-reason).
- [ ] Rehearse the demo below 3 times, with the kill landing somewhere different each time.
- [ ] Rehearse the live fixes from [phase 4](phase-4-signals-cost.md) once more.

## Demo, 3 minutes

| Time | What the judges see |
|---|---|
| 0:00–0:15 | One line: who pays for this job today. Type the carrier name the judges give, or paste their request. |
| 0:15–1:15 | Checks stream in. Click GST status: the field is highlighted, with its date and cost. Discovery shows the GSTINs found and the PAN they share; the lookalike sweep shows near-identical names side by side. |
| Kill | Press "kill worker" (or `kill -9` in the terminal); the page freezes mid-step. Restart: "resumed at `<step>` · 0 paid calls repeated · ₹0 re-spent". If a call was in flight at the kill, the line says so. |
| 1:15–1:50 | The approver card: facts, links, flagged lines, gaps, "Account holder not verified", rupees. Approve. Then edit the bank account: the approval goes void and the job is awaiting approval again. |
| 1:50–2:30 | The vendor card in Hindi: the reason, its source, how to contest. Any Cannot verify names what's missing, and the drafted ask is shown, not sent. |
| 2:30–3:00 | "This job cost ₹X, measured from every call: Crustdata, GST and Haiku. We'd charge ₹Y per carrier." |

## Questions to prepare

**Design**

- **100× volume:**
  - Workers already hold no state; they read jobs from the store, so you can run more of them.
  - Each source gets its own rate limit and a shared cache.
  - Crustdata batch Enrich handles bulk onboarding drives.
  - The real bottleneck is the person approving. Batch the clean Clears for them and send everything else to full review.
- **A second client:** a new `client_id` with its own `rules.yaml`, templates, keys and vendor master. Only the code is shared.

**Ethics**

- **What personal data do we keep?**
  - Aadhaar is never stored.
  - PAN, phone, address line and account number are masked; the account number is kept only as a hash and its last 4 digits.
  - People who didn't match are not stored.
  - Everything sits in one SQLite file on our machine.
- **Can the model approve, pay or email?** No. It reads and extracts, code decides, a person approves, and nothing executes afterwards.
- **What about a director who shares a name with someone in the news?** The anchor rule: 2 or more anchors give Refer, 1 is a gap, 0 means nothing is stored.
- **How does a rejected carrier contest?** The vendor card names the document to send.
- **What about CAPTCHA portals?** A person looks them up; the agent never tries to get past one.

**Finance**

- **Buyer:** the 3PL's procurement head, who clears new carriers every month.
- **Price:** the measured median rupees per job plus a margin, set against what it costs today (an outside firm's fee per vendor, or staff hours) and against one advance lost to a fake carrier.
- **Be honest about the limit:** we catch changed bank details and payee-name mismatches, but we don't verify the account holder. That would be a paid add-on, a penny-drop check at about ₹1–3 per call.
- **Measured cost:** from the `calls` table, every call priced, including GST and Haiku.

## Done when

- [ ] `logs/eval.txt` and `logs/kill_test.txt` are saved, with no wrong verdicts and 20 out of 20 kills passing.
- [ ] The README is final.
- [ ] The demo has been rehearsed 3 times.
