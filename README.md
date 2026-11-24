# dsh-code-review

A DeepSeek Harness (DSH) plugin that provides `/code-review`, modeled on Claude Code's
`code-review` plugin: several independent reviewers inspect the change, every candidate finding is
scored for **confidence** and given a separate **severity** (高 / 中 / 低), and every confirmed
finding is reported — highest severity first, under 🔴 / 🟡 / 🟢 sections.

## What it does

`/code-review` reviews:

- the current branch's commits ahead of its upstream plus uncommitted, staged, and untracked changes;
- a pull request by number or URL (via `gh`);
- a branch or revision range (`main...feature`, `abc123^..abc123`);
- a path, file, or directory.

Flags:

| Argument | Effect |
|---|---|
| `low` / `medium` / `high` / `max` | Effort. `low` runs one reviewer pass on chunk diffs (no packs) and **skips the verifier wave**, labelling the report `未独立验证`; `medium` (default) runs the pack-based reviewer pass, settles its `DEEP:` items in place, and verifies; `high`/`max` add a maintainability pass. Effort never decides whether a *confirmed* finding is reported, only how much of the change is reviewed and how hard the findings are checked. |
| `--comment` | Post the findings as one PR/MR comment (GitHub `gh`, GitLab `glab`). |
| `--max-findings <n>` / `all` | Cap the report (default 32). |
| target | PR, branch, range, or path. Omitted: the current branch. |

## Workflow

The instructions in [`assets/code-review.md`](assets/code-review.md) implement the Claude Code
pipeline:

1. **Eligibility** — for a PR: closed, draft, trivial/automated, or already reviewed and unchanged.
2. **Guidelines** — collect the root `CLAUDE.md`/`AGENTS.md` plus every one whose directory contains a
   changed file; only rules scoped to the file count.
3. **Summary** — capture the intent from the PR title/body or the commits.
4. **Reviewers, one pass** — a large diff is split once into balanced chunk files (~900 diff lines
   each) and one **reading pack** per chunk is built: every changed file with the scope around its
   hunks, `+` marking added lines, test files reduced to hunks, and each module's **importers** (the
   files that import it) so "who calls this" needs no exploration. One reviewer per chunk, at the
   session's own model and default effort, settles its own `DEEP:` items in the same turn; there is no
   second deep wave. History/blame and prior discussion are separate and skipped when they cannot pay
   for themselves.

5. **Independent verification, routed by category** — after the reviewer wave, all verifiers launch
   in one message (at most eight agents, at most two candidates each, at most twelve candidates
   total). Each scores confidence that the defect *is real* 0–100 plus a separate
   `severity: high | medium | low`. `bug`, `guidelines`, `history`, and `prior-review` candidates
   always get a verifier; `comment-contract` is verified too unless it is *pure wording* — a comment
   that states a lock, ordering, transaction or ownership invariant is the evidence, not a docs nit.
   Only `maintainability` is self-checked. Structural hazards — a second connection inside a
   transaction, a lock held across an unrelated await, a lock-order inversion — are **high even when
   they need load to manifest**. Only rejected, unverifiable, and below-75 findings are dropped, and
   a confirmed finding is never discarded for being minor. Under `low` there are no verifiers at
   all: every candidate is checked by one re-read and the report says it is unverified.
6. **Report** — in **Chinese by default** (another language only when requested): a merge verdict
   with the 高/中/低 counts, a `验证状态` line, a 改动概要, findings grouped under 🔴 高 / 🟡 中 / 🟢 低
   (each with `影响` / `证据` / `建议` sub-bullets and a `path:line` link), a 未解决 list for every
   unsettled `DEEP:` item, a 审查范围 section, and a closing sentence stating the cap, the count, and
   what was not checked.

The review is **strictly read-only**: it never edits, creates, deletes, or commits repository
files, and never runs formatters or code generators. Applying the findings is a separate task the
user must request after reading the report.

False positives are excluded explicitly: pre-existing issues, unchanged lines, lint/typechecker
catches, style nitpicks, silenced guideline violations, and input-dependent speculation.

## Wall-clock cost

Wall-clock time is set by model turns, not by how much is read: an agent's duration is roughly its
turn count times the 3–6 s a round trip takes, and a parallel wave ends with its slowest member. The
harness caps live subagents (default **eight**, a host setting) and **rejects** rather than queues
beyond the cap, so the skill sizes every wave to that number.

Measured on a real review (one measured PR: 149 files, +13,325/−344, **23.1 minutes** end to end):

| Phase | Time | Turns per agent |
|---|---|---|
| Resolve, diff, guidelines, intent | 2.6 min | ≤ 30 |
| Reviewer wave (7 reviewers) | 11.3 min | 64–133 |
| Verification wave (12 verifiers) | 7.7 min | 9–38 |
| Report | ~1 min | — |

The reviewers, not the verifiers, were the cost: each spent 76–132 tool calls, mostly one `sed -n` or
one `grep` per message. The verifiers were quick individually, but 12 of them needed two batches
(8 ran, 4 waited for a slot), which added five minutes on its own.

The skill therefore prescribes:

- the diff, its chunks, one **reading pack** per chunk, and the quoted guidelines are prepared once and
  shared by path — a reviewer reads its chunk in 1–2 calls instead of hunting;
- the wave deadline is mechanical (at most two `wait_agent` calls, then proceed and mark the silent
  chunk partial), and an oversized pack is re-split before launch — one run's 831 s straggler against
  a 340 s median made the wave 13.8 of 16.7 minutes;
- the chunk count comes from `ceil(lines / 900)` clamped to `[2, cap−2]`, not from the file count;
  smaller chunks measured better on both time and findings (the same 6,580-line PR: four big chunks
  gave three findings and a 7.1-minute wave, eight ~820-line chunks gave ten and a 5.7-minute wave);
- there is one reviewer pass, and a reviewer settles its own `DEEP:` items in place — a separate deep
  wave measured 9.3 of 22.3 minutes on its own;
- verification is one batch launched when the wave is done: at most eight verifier agents, two to
  three candidates each, twelve candidates total; only `maintainability` and pure-wording
  `comment-contract` are checked without a verifier.

**Second real run, after the turn-economy pass** (the same 149-file diff again, 10.2 minutes):

| Phase | Time | Notes |
|---|---|---|
| Prep | 1.8 min | diff, 6 chunks, guideline file, then one wave (measured before reading packs existed) |
| Reviewer wave (8) | 6.6 min | 8–19 turns, 19–42 calls, 20–66k output tokens each |
| Verification (3 verifiers) | 1.1 min | the rest were `comment-contract`, self-checked |
| Report | ~0.2 min | todo list ended fully completed |

It was 2.3× faster but **found 5 candidates instead of 15**: the reviewers sliced files instead of
reading them, and chunks held up to 35 files — the time came out of coverage, not out of waste. That run
is why the skill now hands reviewers reading packs (Step 1), keeps chunks at ~900 diff lines, and keeps the
single reviewer pass in place of the skim/deep/verify triple wave. The frontier it works inside: 22.3
minutes for three serial waves on a 129-file PR, and the one-pass design with pre-computed importers
targets ~10–12 minutes with the same file-level context (being measured).

**A/B check of the reviewer prompt** (same chunk: `frontend` + `specs`, 3,008 diff lines, same model):

| | Before | After |
|---|---|---|
| Duration | 398 s | 183 s |
| Turns / tool calls | 64 / 76 | 10 / 21 |
| Output tokens | 51k | 36k |
| Cache-read tokens | 7.2M | 0.7M |
| Findings | 5 candidates | 4 findings (2 of the same defects, 2 new) |

Under `low` the verification wave disappears entirely — a PR this size lands near 5–6 minutes, with the
verdict still delivered and labelled `未独立验证`.

## Reviewer phase

The reviewer phase is where a review spends its minutes, so it has six deliberate mechanisms:

| Mechanism | What it buys |
|---|---|
| **Small and middle diffs get one reviewer** | a 309-line, three-file change measured 6.2 min in a four-reviewer wave; an 11-file, 666-line change measured 13.7 min as five agents (two primaries, two second readers, history) — below ~25 files / ~1,200 lines a single reviewer covers the four code lenses in ≤ 30 calls |
| **Reading packs** (`assets/pack-review.py`) | each reviewer reads its whole chunk in 1–2 calls instead of hunting with `sed -n`; packs over ~2,200 lines arrive as parts, never truncated |
| **`low` effort only on a single small pack** | with a ≤800-line pack, `low` halved the wave (391 s → 195 s) at the same conclusion; on a 1,700-line chunk it cost the same 270 s and missed a confirmed defect, and without a pack it saves nothing. Always pass `provider`+`model` with an effort override |
| **Dual readers on high-risk chunks** | a second reader with the complementary framing joins two or three riskiest chunks, in the same message, but only above four chunks: on an 11-file, 666-line PR two second readers took 505 s and 449 s, set the 8.4-minute wave and returned nothing |
| **Behavioural contracts are `bug`, not docs** | a falsified lock/ordering/transaction comment goes to a verifier; on a 92-file PR six candidates were self-checked as `comment-contract` and the one structural hazard among them never reached a verifier |
| **Verifiers batched, not trickled** | the median spread between first and last verifier spawn was 106 s over sixteen runs, and one run spawned 18 verifiers over five messages across 366 s; verifiers are grouped 2–3 per agent, at most eight agents, launched in one message |
| **Verification in one batch** | all verifiers launch in a single message when the wave is done; trickling them one message per candidate measured a 106 s median spawn spread and a 366 s worst case |
| **`DEEP:` items settled in place** | the reader that raised a question answers it in its own turn, from the pack's importers and one whole-file read; only genuinely unsettled items reach the report's 未解决 list |
| **Tests are assertion checks** | a third of a large diff is tests; they are checked, not studied |
| **Scheduling** | launch order is heaviest-first (LPT) when the wave exceeds one batch; a PR with no review comments skips the prior-discussion reviewer; history/blame covers the 10 most-changed non-test files |
| **The fast model finds, the strong model judges** | discovery: the default model at high effort found a confirmed defect in 277 s where a stronger, slower model took 697 s and found none; judgment: the stronger model confirmed the same defect at 88 in 145 s against the default's 82 in 107 s — the 2.5x penalty is an open-ended-exploration cost, not a model-wide one |
| **Waves sized to the host cap** | `maxActiveSubagents` (Settings → Subagent → Limits) defaults to 8 children and **rejects** rather than queues, so a wave above it fails instead of waiting; `workflow` admits through the same pool and one rejected `agent()` aborts the run, so it is only for pipelining once the cap is raised |

Two levers stay with the caller: `low` effort drops the verification wave entirely (the verdict still
comes out, marked unverified), and lowering the session's reasoning effort shortens every step
(subagents inherit it; reasoning is most of the output tokens per turn).

## Merge verdict

Every review of something that can land — a PR/MR, a branch, a revision range — opens with one of five
verdicts, so "can I merge this?" is always answered. A path target is answered for that path only.
The verdict line is followed by a `验证状态` line (`独立验证` / `部分独立验证` / `未独立验证（low triage）` / `无候选`),
so the opinion never reads stronger than the checking behind it.

| Verdict | When |
|---|---|
| **Ready to merge** (可以合并) | 0 高, 0 中. 低 findings are still listed and counted. |
| **Fix after review** (修复后合并) | 0 高, at least one 中 — a user-visible wrong behaviour or a hot-path cost that must be fixed inside the change. |
| **Fix before merge** (合并前必须修复) | At least one 高 severity finding. |
| **Do not merge** (不建议合并) | A 高 is structural (design, scope, public contract, migration), or an earlier 高 is unresolved. |
| **Cannot judge** (无法判断) | Coverage is incomplete: chunked review gaps, missing forge metadata, or a potential 高 that could not be established. |

Severity is three-tier and the report groups findings under 高 / 中 / 低, with the counts in the verdict
line. A **高** is a verified finding the verifier judged `severity: high` at a score of at least 75 —
wrong result, data loss, security exposure, crash, unreachable feature, regression, a plainly violated
written guideline, or a structural hazard such as a lock held while acquiring a second connection. A
**中** is a user-visible wrong behaviour that needs a condition (a lost update on a race, a mode that
hands downstream nothing), or a hot-path cost the change introduces. A **低** is convention drift,
cold-path waste, duplication, or a stale comment. Under `low` effort these come from the orchestrator's
own re-read and the report says they are unverified. A confirmed finding that is merely minor is still
reported, in the 低 section — "ready to merge" is never a clean bill of health. Every verdict states that build, tests, runtime behavior, and approvals were **not** checked,
so it cannot be read as a CI or security sign-off.

## How it is wired into DSH

DSH plugins are Cordis plugins; an installable bundle declares `dsh.bundle.patch`. This package is
both:

- [`cordis.patch.yml`](cordis.patch.yml) inserts one plugin row, `code-review-skill`;
- [`lib/index.js`](lib/index.js) registers a `SkillProvider` on `ctx.skills` that exposes the
  `code-review` skill from [`assets/code-review.md`](assets/code-review.md).

The skill is user-invocable (it appears in the `/` menu) and model-invocable. The body is re-read on
every load, so editing the Markdown takes effect on the next invocation without a rebuild or
restart. The catalog description lives in `lib/index.js` and changes only on plugin reload.

## Install

The plugin is installed from its local path into the active profile:

```text
plugin_manager install_bundle  target=<plugin-dir>
```

or from a shell, from the profile directory:

```bash
dsh plugin --profile <profile> add <plugin-dir>
```

It is profile-wide: every session using that profile gets `/code-review`. To remove it, deselect the
bundle in the Plugins page or run the matching `plugin_manager remove_bundle`.

## Usage

```text
/code-review
/code-review low
/code-review 1234
/code-review --comment 1234
/code-review main...my-feature
/code-review src/auth
/code-review --max-findings all HEAD~15..HEAD
```

In any host, the review is a regular chat report. There is no findings panel equivalent to Claude
Code's `ReportFindings`, and no cloud `ultra` review: `ultra` is treated as `max` with a note.

## Differences from Claude Code

- **No cloud review.** `ultra` cannot run; the local pipeline is used.
- **No findings UI.** Results are Markdown in the conversation, not a host-rendered findings list.
- **Confidence is internal.** Scores are used to filter, not shown in the report.
- **PR comments are one summary comment.** Claude Code's inline-comment MCP tool has no DSH
  equivalent here; `--comment` posts one comment with full-SHA code links.
- **Adds a merge verdict.** Claude Code reports findings; this plugin also answers "can this land?"
  for every PR/MR, branch, and range — with a `验证状态` line and its review-only caveat.
- **No `--fix`.** Unlike Claude Code, the review never modifies code; applying findings is a
  separate follow-up task.
- **Report language is Chinese by default.** Ask for another language explicitly to switch.
- **Default cap is 32 findings.** Use `--max-findings all` to see everything.

## Layout

```text
code-review/
├── package.json          # dsh.bundle.patch + main entry
├── cordis.patch.yml      # inserts the code-review-skill row
├── lib/index.js          # SkillProvider registration
├── assets/code-review.md # the review instructions
├── assets/split-diff.py  # splits a large diff into balanced chunk files
├── assets/pack-review.py # builds one reading pack per chunk
├── INSTALL.md            # 中文安装与使用说明
└── README.md
```

## Provenance

The workflow and confidence rubric follow Anthropic's `code-review` plugin
(`anthropics/claude-code`, `plugins/code-review`) and the `/code-review` documentation for Claude
Code. This package is an independent DSH implementation of that logic.
