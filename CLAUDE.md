Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

## 5. This project: Carrier Clearance

An agent that checks a new carrier (truck operator) for an Indian 3PL. It takes a carrier name or an onboarding request and returns a sourced verdict (Clear, Refer, Reject or Cannot verify), a vendor card and an approver card.

**Hackathon build:** 2 builders, about 3 hours. Build only what the demo needs. Don't add production hardening (caching layers, multi-tenant code, retry chains, extra abstractions) unless asked.

### The plan

- Start with [plan/00-overview.md](plan/00-overview.md): decisions, architecture, data contracts, the file list, and the rehearsal set.
- Work phase by phase: `plan/phase-0-setup.md` … `plan/phase-5-rehearse-demo.md`. Tick the checkboxes as tasks are done.
- The decisions in the overview's §3 are settled. Don't reopen them without a concrete reason.
- `carrier_clearance_build_plan.md` is the old 7-hour plan. Use it for background only: it still has the payout, the outbox and the 4-builder schedule, all of which were dropped.

### Hard rules (don't break these)

- **Agents read and extract; code decides.** Model output is schema-checked and never sets a verdict or triggers anything.
- **No source, no claim.** Every fact needs a field path or exact quote found in a stored raw response. The validator drops anything else.
- **Mask before the model.** Aadhaar is dropped. Phone, address line, director and owner names, and the account number (stored as a hash plus its last 4 digits) are masked in the database, logs and prompts.
- **Checkpoint every step.** Outside calls are keyed by `sha256(provider + request)`; a stored response is reused and never fetched again.
- **Nothing executes after approval.** There's no payment, no email sending and no vendor-master writes. Approval is only a row bound to the verdict hash.
- **Decision order:** Reject → Refer → Cannot verify → Clear. Clear needs c1, c2, c3, c4 and c9.
- **Rules are data.** Thresholds, providers, prices and limits live in `rules.yaml`, and card sentences in `templates.yaml`. Don't hard-code them.
- **One serializer.** Output JSON is built only in `cc/output.py` `serialize()`.
- **Fixtures only with `MOCK=1`.** Mock facts are tagged and the page shows a banner. Stage runs are live.

### Stack and layout

- **Stack:** Python 3.12, FastAPI and uvicorn, sqlite3 (WAL), httpx, pydantic, PyYAML, the Anthropic SDK.
- **Model:** `claude-haiku-5-5` for every model call.
- **Front end:** a single `web/index.html` with plain JS that polls the API. No build step.
- **Files:** keep to the list in overview §4.6 (`cc/` has 10 modules). Put new code into an existing module before creating a new file.
- **Keys:** in `.env`, with `.env.example` as the template. Never commit `.env` or print keys.

### Commands (once they exist)

- `uvicorn cc.app:app --reload`: the web app; it starts the worker subprocess.
- `pytest tests/`: unit tests.
- `python scripts/eval.py`: the rehearsal set; must show no wrong verdict before a phase is done.
- `python scripts/kill_test.py`: 20 random kills; must pass before a phase is done.