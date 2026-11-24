# Code review

Run the `/code-review` workflow: find high-signal problems in a change, verify every candidate independently, and report only the findings that survive verification. The review is strictly read-only: it reports findings and suggested fixes, and never edits, creates, deletes, or commits repository files.

## Parse the argument line

Everything after `/code-review` is one argument line:

```
/code-review [low|medium|high|max] [--comment] [--max-findings <n>|all] [target]
```

- **effort** — `low`, `medium`, `high`, or `max`. Default `medium`. Effort widens the review; it never decides whether a *confirmed* finding is reported.
  - `low`: one reviewer pass, hunks only, **no verifier wave**; the report says `未独立验证`. Fastest; read its findings as candidates.
  - `medium`: run reviewers 1–6. For a chunked diff this means the four code-facing lenses per chunk plus the two cross-cutting reviewers — see *Chunked reviews*; for a small or middle diff it is one reviewer covering the four code-facing lenses — see *Small and middle diffs*.
  - `high` / `max`: reviewers 1–5 plus the maintainability pass.
  - `ultra` is Claude Code's cloud review. It has no local equivalent; treat it as `max` and say so once.
- **`--fix`** — not supported. The review is read-only; if the user passes it, ignore it and say the review does not modify code.
- **`--comment`** — post the findings to the pull request (GitHub via `gh`, GitLab via `glab` when the target is a merge request).
- **`--max-findings <n>` / `--max-findings all`** — report at most `n` findings, or all of them. Default **32**.
- **target** — a PR number or URL, a branch, a revision range (`main...feature`, `abc123^..abc123`), or a file/directory path. Everything left after the flags is the target, even when it starts with another command name.

If this skill was loaded without an argument line, assume `medium` and review the current branch.

## Non-negotiable rules

1. **Read-only.** Never edit, create, delete, rename, or commit repository files during the review, and never run a formatter or code generator. Report findings and suggested fixes only; applying them is a separate task the user must request after reading the report.
2. **High signal only.** Flag an issue only when you can name the concrete scenario in which it fails: it will not compile/parse, it produces a definite wrong result, or it plainly violates a written guideline that applies to that file. If you are not certain, do not flag it.
3. **Only changed lines.** A real problem on a line the change did not introduce is out of scope.
4. **Cite what you verified.** Every finding names `path:line`, the evidence you read, and the failure it causes. Never invent a line, a rule, or a command result.
5. **Do not run builds, typecheckers, linters, or tests.** Assume they run elsewhere. Compile errors that are certain from reading the diff are still valid findings.
6. **Subagents are read-only.** They must not edit, commit, or run the build. They may read files and run `git`/`gh` read commands.
7. **No attribution footer.** Never add "Generated with …", an agent signature, or a co-author line to a PR comment, commit, or report.
8. **Turn economy.** Wall-clock time is what each agent generates plus the round trips it takes — not how much it reads. Batch independent tool calls, read prepared artifacts instead of re-deriving them, and stay inside the per-agent budgets in *Wall-clock budget*.

## Wall-clock budget

Wall-clock time is **what each agent generates, plus how many round trips it makes**. Measured across fifteen reviewer runs: duration ≈ output tokens ÷ ~170 tok/s + turns × ~3 s. At a high reasoning effort most of the output is thinking, so the wave's wall clock is mostly thinking — and a parallel wave ends with its **slowest** member, so adding reviewers never shortens a wave.

Two runs of the same PR make that concrete. Seven reviewers at 64–133 turns each spent 11 minutes in the wave and returned 15 candidates. Eight reviewers cut to 8–19 turns spent 6.6 minutes and returned 5 — the minutes came out of coverage, not out of waste, because they also stopped reading whole files. The fix is therefore not fewer reads: make each turn cheaper (a pack, fewer round trips) and each reviewer shallower in thinking but not in reading:

- **A pack beats a worktree.** Give the reviewer one file holding its chunk's changed lines inside their enclosing scope (Step 1) and it reads the whole chunk in 1–2 calls. Give it a diff plus a worktree and it spends the budget hunting, then reports what the diff already showed.
- **Settle `DEEP:` items in place.** A reviewer that raises a question answers it in its own turn, from the pack's IMPORTERS section and one whole-file read. A separate deep wave re-reads the same context and costs its own duration again — measured at 9.3 of 22.3 minutes on one PR.
- **Batch tool calls.** 4–8 independent `read` / `grep` calls in one message. One small call per message is the usual reason a review takes 15 minutes instead of 5.
- **Read the prepared artifacts.** Steps 1 and 2 write the diff, its chunks, a pack per chunk, and the quoted guidelines to files. Read those paths; never re-run `gh pr diff`, `git diff`, or `git show`, and never re-derive what was already collected. When you do open a repository file, open it whole — a slice hides the context that turns a suspicion into a finding.
- **Tests are assertion checks, not the target.** A third of a large diff is tests. Ask whether an assertion matches the new behaviour and move on; never count missing coverage.
- **A wave is capped by the host, and the cap rejects rather than queues.** `maxActiveSubagents` (Settings → Subagent → Limits) defaults to **8** live children per tree. At capacity a new child is **rejected** with `ACTIVATION_LIMIT_REACHED`; it does not wait for a slot. Keep every wave at or below the cap, start heaviest first, and ask the user to raise the setting when the change genuinely needs more readers at once.
- **Reasoning effort is the biggest single lever, but only with prepared context.** Subagents inherit the session's effort. On a pack, `reasoning_effort: 'low'` halved the wall clock (391 s → 195 s, 65k → 32k output tokens) for the same conclusion; on a raw diff plus worktree it saved nothing and lost findings, because a cheaper thinker hunts instead of thinking. So: pack first, then downgrade the effort. Without a pack, keep the default.
- **Depth is the budget that buys findings.** Cutting reads buys minutes and loses defects. Keep the reading; cut the turns, the slicing, and the effort.

| Agent | Tool calls | Messages carrying tool calls |
|---|---|---|
| Orchestrator before the reviewer wave | ≤ 20 | — |
| One chunk reviewer (pack, settles its own `DEEP:` items) | ≤ 40 | ≤ 10 |
| One small-diff reviewer (all lenses) | ≤ 30 | ≤ 10 |
| Second reader on a high-risk chunk (contracts and cost) | ≤ 25 | ≤ 8 |
| One verifier | ≤ 15 | ≤ 5 |

## Step 0 — plan

Call `todo_write` with the steps below before doing anything else, then keep it honest as you go: mark a step `in_progress` when you start it and `completed` the moment it is done.

Plan the **work only** — resolving, reading, reviewing, verifying, and finalizing the findings and the verdict. Never put "write the report" in the list: the report ends the turn, so that item can never be checked off and the user is left staring at a spinner next to a report that already landed. End the list with the analysis step that settles the findings, for example "Finalize verified findings and choose the merge verdict".

**Before** you send the report, rewrite the whole list with every item `completed` — no `pending`, no `in_progress` — and give a partially covered chunk its own named item. Only `todo_write` changes what the user sees, so a list nobody rewrites freezes at plan time and reads as "still running" long after the review finished.

## Step 1 — resolve the target and collect the diff

Work from the repository root (`git rev-parse --show-toplevel`). Read the actual diff; do not review from memory or a summary. **Do the whole prep in three tool calls, not seventeen.** Measured in one run: prep took 1.4 minutes across 17 small bash/read calls — every call is a model turn, and the budget is dominated by that latency, not by git. Batch it:

```bash
# call 1 — everything that produces artifacts (one shell)
set -e; cd "$(git rev-parse --show-toplevel)"
gh pr view <n> --json number,title,body,state,isDraft,headRefName,baseRefName,headRefOid > /tmp/pr<n>.meta.json
git fetch -q origin pull/<n>/head:refs/review/pr<n>
git worktree add -q --detach /tmp/pr<n>-review refs/review/pr<n> 2>/dev/null || git -C /tmp/pr<n>-review reset -q --hard refs/review/pr<n>
git diff $(git merge-base HEAD refs/review/pr<n>)..refs/review/pr<n> > /tmp/pr<n>.diff
python3 <assets>/split-diff.py /tmp/pr<n>.diff /tmp/pr<n>-chunks --chunks <N>
python3 <assets>/pack-review.py /tmp/pr<n>.diff /tmp/pr<n>-review /tmp/pr<n>-chunks /tmp/pr<n>-packs
cat /tmp/pr<n>-chunks/manifest.md | head -40; grep -h '^- pack' /tmp/pr<n>-packs/packs.md

# call 2 — the rules (one read each, only files that exist)
# call 3 — write /tmp/pr<n>-intent.md (3-6 lines)
```

**Do not read the diff or the source yourself during prep.** The reviewers do that; the orchestrator needs only counts, the file list, and the guidelines. Measured in one run: a 1.6-minute prep spent about a minute in `cat /tmp/pr<n>.diff` plus a dozen `sed -n`/`grep` calls through the router and release service — work the reviewers then repeated. Prep should be: resolve, fetch, split, pack, collect the rules, write a 3–6 line intent. Nothing else.

**Start with the commands, not with planning prose**: one run spent 2.6 minutes in a single reasoning turn before its first tool call, which is a third of its prep and none of its work. Resolve, fetch, split, pack — think about the reviewers while those run.

### No target: current branch

The review covers the commits ahead of the upstream branch plus uncommitted, staged, and untracked changes.

```bash
git status --porcelain=v1
git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null
git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null
```

Pick the base: the upstream branch when one exists; otherwise `origin/main`, `origin/master`, `main`, `master`; otherwise the previous commit (`HEAD~1`) when it exists; otherwise review only the working tree.

```bash
BASE=$(git merge-base HEAD <base-ref>)
git diff --stat "$BASE" HEAD
git diff "$BASE" HEAD
git diff            # unstaged
git diff --cached   # staged
git ls-files --others --exclude-standard   # untracked
```

Treat each untracked text file as an added file in the review (read it; skip binaries, lockfiles, and generated output). If the whole diff is empty, say so and stop.

Prepare the diff file, the chunks (one is enough for a small diff) and the head worktree as described in *Prepare the artifacts once* below, and say which chunks you covered when there is more than one.

### Target is a pull request

Use the forge CLI: `gh` for a GitHub PR (installed and authenticated, `gh auth status`) or `glab`
for a GitLab merge request. Use the base and head commits, not the local branch, so the review
matches what would merge.

```bash
gh pr view <number> --json number,title,body,state,isDraft,author,baseRefName,headRefName,baseRefOid,headRefOid,url
gh pr diff <number> > /tmp/pr<number>.diff   # keep it: reviewers read the file, not the API
gh pr view <number> --comments
```

Skip when the PR is closed, is a draft, is trivially automated, or already has a review comment from this workflow and nothing changed since. If the user explicitly asked for a re-review, continue instead of skipping.

For a GitLab merge request (URL or `!123`), use `glab mr view <id>` and `glab mr diff <id>` instead.

If neither forge CLI is available, stop and report that PR review needs the GitHub CLI (or `glab`).
Do not substitute web fetch.

### Target is a range or branch

```bash
git log --oneline <range>
git diff <range>
```

A single branch name means `<default-base>...<branch>`. A single commit hash means `<hash>^..<hash>`.

### Target is a path

```bash
git diff [<base>] -- <path>
git ls-files --others --exclude-standard -- <path>
```

Review only the changes under that path, with the same base rules as the current branch.

### Prepare the artifacts once

Every reviewer must be able to start reading before its first `git` call, whatever the size. Prepare the artifacts in one `bash` step and pass the paths: an artifact prepared here costs one `read` instead of one exploration per reviewer. A small diff simply yields one chunk and one pack.

1. **A head checkout.** When the PR head is not local, fetch it and add a read-only worktree, so reviewers read full files at the head rather than the base:

   ```bash
   git fetch origin pull/<n>/head:refs/review/pr<n>
   git worktree add --detach /tmp/pr<n>-review refs/review/pr<n>
   ```

2. **The full diff and its stats.**

   ```bash
   gh pr diff <n> > /tmp/pr<n>.diff          # or: git diff <base> <head> > /tmp/pr<n>.diff
   git diff --numstat <base> <head>          # per-file changed lines, for judging risk
   ```

3. **The chunks and their manifest.** Run the splitter shipped with this skill (resolve its path against the skill's base directory). It packs files by directory into balanced chunks and writes `chunk-NN.diff` plus `manifest.md`:

   ```bash
   python3 <skill-base>/split-diff.py /tmp/pr<n>.diff /tmp/pr<n>-chunks \
       --chunks <chunks> --max-files 30
   # a small or middle diff (<= ~25 files, <= ~1,200 changed lines): --chunks 1
   #
   # chunks = ceil(diff_lines / 900), clamped to [2, cap - 2], where cap is the value in
   # Settings -> 子智能体 -> 运行限制. Read the value; the host default is 8. Reserve two slots for verifiers.
   # e.g. 2,500 lines -> 3 chunks; 6,600 -> 8; 24,000 -> 14 at the cap (~1,700 each)
   ```

   **Size the chunk count from the concurrency cap, not from the file count.** `--max-files` does not decide the count — on a 232-file diff it silently turned an eight-chunk plan into sixteen, which at a cap of 12 became two batches, and the measured wave was 14.1 of 20.9 minutes. Admission **rejects** beyond the cap rather than queueing, so a wave that exceeds it either fails or has to be split into sequential batches, and both cost more than bigger chunks.

   The formula: `chunks = ceil(diff_lines / 900)`, clamped to `[2, cap - 2]` — the two spare slots are for verifiers that start while the wave is still running. Take `cap` from the host setting, not from a guess. **Smaller chunks measure better on both counts.** Two runs of the same 6,580-line PR: four chunks of 1,643 lines produced packs of 2,100–4,100 text lines, five candidates and three findings, with the wave at 7.1 minutes; eight chunks of ~820 lines produced packs of 1,400–1,800, twelve candidates and ten findings, with the wave at 5.7 — each reader had less to hold, so it concluded more, and the slowest reader was faster. Do not grow chunks to save reviewers; do not shrink below ~700 lines either, where an earlier run without packs returned two candidates from nine chunks.

   **Guard on the pack, not only the diff.** When `packs.md` reports more than ~2,200 text lines for a chunk, that chunk needs splitting even if it means more chunks than `cap - 2`. A diff beyond ~15,000 lines will not fit one batch at cap 12, and beyond ~20,000 at cap 16: say so, and ask the user to raise `maxActiveSubagents` (16 → 20) if they review changes that size regularly — one batch of 18–20 chunks of ~1,300 lines beats 14 chunks of 1,700 on both depth and the slowest reader's duration.

   Read the manifest and balance the reviewers. `--max-files` guards against a chunk of sixty tiny files; the line budget is what matters, and ~900 diff lines is the target. Eight chunks plus the two cross-cutting reviewers are ten agents — one batch when `maxActiveSubagents` is at least ten, two short batches when it is still eight, which is still better than oversize chunks. At `low` effort pass `--chunks 4` and no file cap: triage reads hunks, not files. The manifest also lists test files and a **launch order, heaviest first** — use it when the wave is larger than one batch.

4. **The reading packs.** A reviewer that is handed its chunk as one readable file does not hunt; a reviewer given a diff and a worktree does. Build one pack per chunk — every changed source file with the scope around its hunks, added lines marked `+`, test files reduced to their hunks:

   ```bash
   python3 <skill-base>/pack-review.py /tmp/pr<n>.diff /tmp/pr<n>-review /tmp/pr<n>-chunks /tmp/pr<n>-packs
   ```

   Pass each reviewer the pack path from `packs.md` instead of the chunk diff. A chunk whose pack exceeds ~2,200 lines arrives as parts (`pack-05-1.md`, `pack-05-2.md`) to be read in order, so nothing is silently dropped. Skip the packs at `low` effort — triage reads the chunk diff, and a pack would only bloat its context.

   **Take the reviewer's file list from the pack it will read.** The pack's `### <path>` headers are the truth; a brief built from the manifest or from memory can name files the reviewer does not have. Measured in one run: one reviewer spent its turn reporting that its pack held 18 files but the brief listed files from a different chunk — an entire reviewer wasted, and nothing in that chunk reviewed. Extract the `###` paths from each pack and paste those into its brief; if you cannot extract them, give the reviewer the pack path and no file list at all.

   **Do not let prose crowd out code.** A large PR often carries specs or design docs (one measured 2,246 lines of design document inside a 16,640-line diff); mixed into code chunks they consume reader budget without producing findings. Put documents (`specs/`, `docs/`, `designs/`, long `*.md`) into one chunk of their own, review that chunk for statements the code now contradicts, and say in the scope section that the rest was code.

   **Check the pack sizes before launching.** The wave ends with its slowest reader, so one oversized pack sets the whole duration: measured across twelve recent runs, the slowest reviewer ran 1.5–2.4× the median (831 s against 340 s, 681 s against 497 s). If one pack carries more than ~1.8× the median pack, re-split with a larger `--chunks` (or move that chunk's largest files into a chunk of their own) before spawning — one extra reviewer costs less than one straggler.

## Step 2 — collect the guidelines that apply

Find the agent instruction files that govern the changed files:

- the repository-root `CLAUDE.md` and `AGENTS.md`, when present;
- every `CLAUDE.md` / `AGENTS.md` in a directory that is an ancestor of a changed file.

Walk from each changed file's directory up to the root. Do not load instruction files from unrelated directories. Record each file's path and the rules it states, quoted exactly; those quotes are the only acceptable evidence for a `guidelines` finding.

`CLAUDE.md` is guidance for how the code is written, so not every sentence applies to every review. A rule applies only when its directory scope covers the file and its subject is the changed code.

For a chunked review, write these quotes to `/tmp/pr<n>-guidelines.md` — path, scope, and the exact rule — and point every reviewer at that file. One shared file replaces a re-read of `AGENTS.md` and `docs/agents/*` in every chunk, and every reviewer needs the same quotes for the same files.

## Step 3 — summarize intent

Produce a 3–6 bullet summary of what the change does and why: PR title and description when reviewing a PR, otherwise the commit messages and the diff itself. Note the risky areas (concurrency, persistence, auth, error paths, migrations, public API shape). This summary goes into every reviewer prompt so reviewers judge intent, not just syntax.

## Step 4 — launch independent reviewers

Two different things are called "low" here: the `/code-review low` **argument** (one pass, no verifiers, unverified report) and `reasoning_effort: 'low'` **on the spawn** (the model thinks less; the wave still runs). They are independent.

**One reviewer pass.** There is no second, separate deep wave: measured on a 129-file PR, the three serial waves cost skim 7.1 min → deep 9.3 min → verify 2.6 min, and the deep wave was a third of the whole review while doing reading the readers could have done in their own turn. Each reviewer settles its own `DEEP:` items before it reports — a whole-file `read` of the caller the pack's IMPORTERS section points at, not an exploration. Only what it genuinely cannot settle reaches the report's 未解决 list.

1. **Reviewer** — one per chunk, at the session's default effort on the session's own model. `reasoning_effort: 'low'` is justified only for the **single-pack small diff** — one pack of roughly ≤800 lines that one reader takes in whole: there it halved the wall clock for the same conclusion (391 s → 195 s). On a 1,700-line chunk pack the same downgrade cost the same 270 s as the default effort, but the default found a confirmed defect (score 82) where `low` had dismissed the area as unreachable — it spends the saved thinking on navigation instead. Spread over nine chunk reviewers it bought neither depth nor a verdict: eight `low`-effort readers returned two candidates and a dozen `DEEP:` punts. Without a pack the downgrade also saves nothing, because the model spends the saving on more file reads. **An effort override needs an explicit route**: pass the session's own `provider` and `model` on the same call (`list_subagent_models` lists what is authorized), or omit all three fields. `reasoning_effort` alone is rejected with `child LLM reasoning_effort requires both provider and model` — one wave lost all eight reviewers that way. Use the route the session already runs on. A bigger model is not a fix for a vague prompt: measured on one 1,731-line pack at default effort, a stronger but slower model (68 tok/s) took 697 s and returned no findings, while the session's default model (164 tok/s) took 277 s and returned a defect the verifier confirmed at 82 — the stronger model built one explanation that two code paths "are keyed on the same predicate" and never checked the fallback path the change leaves behind. That is one chunk, not a verdict on any model: keep the stronger model for `max`, where a second, slower opinion is what the user asked for, or for the verifier role, and pin `reasoning_effort` explicitly when you do. On this wave breadth beats depth: it must *see* all of its chunk, not think long about one sentence. It reads its pack, settles what it can, reports findings, and leaves only genuinely unsettled areas as `DEEP:` lines.
On a small diff the same preparation still pays: one chunk (`--chunks 1`) and one pack, and **one** reviewer for the four code-facing lenses — see *Small and middle diffs*. It too settles its own `DEEP:` items rather than leaving them for a second wave.

Use the `subagent` tool, one call per reviewer **and per second reader**, **all in a single message** so they run concurrently, **ordered heaviest chunk first** from the manifest's launch order when the wave is larger than the host's `maxActiveSubagents` (default 8 — read the host's setting, do not assume). One message matters: measured in one run, ten reviewers went out in two messages 32 s apart, so five of them started half a minute late and set the end of the whole wave. Do **not** reach for `workflow` to beat that number: it admits through the same pool, and a rejected `agent()` throws and aborts the whole run (measured: `WorkflowError: agent() could not start a child: subagent limit reached`). If `subagent` is unavailable, perform the same passes yourself, sequentially, and say so.

Do not spawn a reviewer that cannot pay for itself: skip the prior-discussion reviewer when Step 1 found no review comments, and give the history reviewer the 10 most-changed non-test files rather than the whole change.

**While the wave runs, do not re-read the change yourself.** The reviewers already have the pack; an orchestrator that opens the same services and DAOs to "get ahead" duplicates their reading, adds tens of thousands of tokens, and — measured — delayed the verification wave by a minute after the last reviewer returned. Your work during the wave is the verify prompts: for each candidate, the exact file, line, claim and the pack path. Read only the lines a candidate cites when you adjudicate it yourself.

If the whole wave comes back with one configuration error, fix the argument and relaunch the same wave once — do not change the plan or the model in response to it.

**Give the wave a deadline, and act on it.** A reviewer's duration is its own thinking time, and one hard chunk can double the median — measured: 831 s against a 340 s median made the wave 13.8 of 16.7 minutes, with the review idle for the rest. You cannot read a clock, so make the deadline mechanical: use `wait_agent` with a timeout of at most 240 s, and after **two** waits (about eight minutes) take what has returned, mark the silent chunk as partially covered in the report, and move on to verification. Say which chunk came back empty rather than waiting indefinitely.

**Do not call `wait_agent` to pass the time — default to zero waits.** After a batch is out, end the turn: the runtime delivers each result as a message and resumes you. Measured in one run: four waits inside a single turn cost ~40 s of a 7.4-minute review whose wave was only two agents, and a run that yields between turns (one run: nine turns) never spins at all. Spend a wait only when the deadline above actually needs enforcing — you were resumed with nothing new and a chunk is still silent — and then at most two, as that paragraph says.

Every reviewer prompt must be self-contained and include:

- the repository root and target description;
- the base and head commits (or "working tree") and either the prepared **reading pack** path(s) with the file list, or — for a small diff — the prepared diff file path (`/tmp/pr<n>.diff`), never "run `git diff` yourself";
- "tests are assertion checks, not the feature you are reviewing";
- the intent summary and the applicable guideline paths and scopes (a pointer to the prepared guidelines file is enough);
- the high-signal bar and the false-positive list below;
- the output format;
- the efficiency block below, with its paths filled in, including the `DEEP:` protocol;
- "Read-only: do not edit, commit, build, typecheck, lint, or test."

Reviewer 1 — **guidelines compliance**. Check the diff against the quoted rules that apply to each changed file. Flag only an unambiguous violation you can quote, with the rule's path. Do not generalize one directory's rule to another. Schema changes get their own check against the repo's migration guide, if it has one: which policy the repo states for adding columns (ORM model sync vs one-off migration files), and whether this change duplicates a definition the model already carries.

Reviewer 2 — **bug scan**. Read the diff, and the callers its IMPORTERS list points at. Look for syntax/type mistakes, missing imports, unresolved references, clear logic errors, broken error paths, and wrong conditions. **Watch the stale gate**: a decision taken from a window, offset or reservation captured earlier must re-check the state that can change since — playback switched off, dialog closed, session ended, reservation released. A late confirmation judged against a window from before the switch-off, or a settlement that runs after the reservation was closed, is a bug even though every line looks right in isolation. Ignore style and anything that needs context you do not have.

Reviewer 3 — **history and blame**. Read `git blame` and `git log` for the changed regions, limited to the 10 most-changed non-test files. Flag a regression of a prior fix, a reverted decision, or a change that contradicts a documented invariant in the history.

Reviewer 4 — **prior review discussion**. When the target is a PR or a commit range with an available forge, read earlier review comments and commits that touched the same files. Flag an issue that was already raised and remains unresolved. Skip cleanly when `gh`/`glab` is unavailable or there is no such history.

Reviewer 5 — **comments and contracts**. Read the comments, docstrings, type declarations, and assertions around the changed code. Flag a change that contradicts a documented contract or makes an existing comment false. When the false contract is about **behaviour** — lock order, transaction scope, ownership, what a caller receives — file it as `bug` with the comment as evidence, not as `comment-contract`: the category decides whether a verifier ever sees it. **Report the behavioural delta, not just the wording**: if a contract now yields different data — a field no longer returned, a mode that hands downstream nothing, a conversation that no longer reaches a consumer — that is the finding. A report that lists only the docstring while the behaviour changed is a miss.

**Flag scope.** A setting documented as "off records nothing" must gate every writer of that data, including background paths — a token consumed on a retry, an async receipt, a cleanup job. Read the flag's description as a spec and find the writer that ignores it.

**Fallback values.** When a query or guard falls back to another row's timestamp, budget or limit, check the fallback where it is thinnest: a brand-new session (no messages yet), an empty list, a first attempt. A fallback to "when the parent row was created" can silently hand a brand-new entity a window or budget of nearly zero.

Reviewer 6 — **efficiency and scale**. Every chunk reviewer runs this lens at every effort, because it is where a "correct" change still hurts production: a query issued per item where one batch would do (N+1); an `EXISTS` or scan added to a list query that runs on every page load; a per-request or per-turn fetch of a payload that never changes (files, blobs, remote objects) with no cache; an unbounded loop over submitted content; the same work repeated for every turn of a conversation. Flag it when you can name the volume that makes it hurt ("every generated file, up to 2 MB each, on every turn"). Do not flag micro-optimisations, and do not flag volume the change did not introduce.

Maintainability pass (`high`/`max` only) — **reuse, simplification**. Flag duplicated logic that already has a home, needless wrappers or branches, and clear wasted work in the changed code, but only when the fix is obvious and behavior-preserving. Do not turn this into a style audit.

### Chunked reviews

When the diff was chunked, launch one reviewer per chunk. It covers the four lenses that need the code in front of it — 1 (guidelines), 2 (bug scan), 5 (comments and contracts), 6 (efficiency and scale) — for its own files only. Add at most two cross-cutting reviewers: reviewer 3 (history and blame, limited to the 10 most-changed non-test files) and reviewer 4 (prior discussion, only when Step 1 found comments to discuss); at `low` effort run the chunk reviewers only. Do not run all six lenses as separate reviewers over every chunk: six reviewers over the same bytes multiply turns and queue time without adding coverage.

### Dual readers on the high-risk chunks

One reviewer per chunk leaves the deepest chunks read once, by one framing. Add a **second reader** to the chunks where a miss costs most — but keep the two framings disjoint so they do not restate each other:

- **Reader A, behaviour** — will this break at runtime? Bug scan, stale gates, state machines, concurrency and lock order, data written vs data shown.
- **Reader B, contracts and cost** — does the change contradict what the repo, the docs or a comment says, and what does it now cost? Guidelines, comments/contracts, efficiency and scale, test assertions.

Both read the same pack; both return at most five findings; the orchestrator dedupes by `path:line` before verification.

**Pick the second readers by risk, not by count.** A second reader on a chunk of types and fixtures buys nothing; one on the migration, the transaction path, or a realtime state machine is where the marginal finding comes from. Default: the two or three highest-risk chunks (largest diff, or a pack containing backend state, concurrency, migrations). **Never below four chunks**: on a two- or three-chunk review, second readers double the wave for code the primaries already hold — measured on an 11-file, 666-line PR, two second readers ran 505 s and 449 s, set the wave at 8.4 minutes, and returned nothing.

**A second reader must not outrun its primary.** Give it the same pack, the same ≤ 25-call and ≤ 8-message budget, and tell it to answer from the pack rather than re-reading the tree; when a second reader runs longer than the primaries it is exploring, not reading.

**They are free on the clock and expensive in tokens.** Parallel readers leave the wave at its slowest member, so the wall clock does not move; the cost is roughly +20–30% output tokens for the whole review. **Chunk count comes first**: size the chunks at ~900 diff lines, clamped by the cap, and only then spend what is left on second readers. It is easy to do this backwards without noticing: measured in one run (9,412 lines, cap 16), the run chose 8 chunks of ~1,180 lines plus three second readers, where the formula gives 11 chunks — so each primary reader held 30% more than it should, the slowest took 406 s, and the three second readers had the least room to add anything. When the arithmetic leaves no room for second readers, take the chunks and skip them — `second_readers = min(3, cap - 2 - chunks - history - prior)`. Never shrink the chunk count to make room for them. Measured in one run: six chunks plus two second readers returned five findings, where the same PR as eight plain chunks returned ten — the second readers added one finding between them, and the two missing chunks cost five.

Measure it: after the run, count the findings that came only from a second reader. If it contributed fewer than one finding per chunk it covered, say so in the report and skip second readers next time for that repository.

**Test files are assertion checks.** A third of a typical large diff is tests. The reviewer asks one question of each test section — does this assertion match the behaviour the change introduces, or does it assert something the change made false — and moves on. It does not read the surrounding test suite, and it never counts missing coverage as a finding.

### Small and middle diffs: one reviewer, not five

A change of at most **~25 files and ~1,200 changed lines** does not need a fan-out. Split it into chunks and you pay the fixed cost of every agent — warm-up, manifest, harness — for a pack one reader could hold. Measured: an 11-file, 666-line change went through two chunk readers plus two second readers; the second readers took 505 s and 449 s against the primaries' 308 s and 295 s, set the whole 8.4-minute wave, contributed no findings, and the review took 13.7 minutes. A 10-file, 411-line change as three reviewers took 6.6.

So for anything up to that size: **one code reviewer** covering the four lenses, reading the single pack (`--chunks 1`), **plus the history/blame reader whenever the diff modifies existing behaviour** (skip it only when the change is essentially new files). The history reader is one more agent in the same message, so it costs no wall clock — and it is where the marginal finding comes from: measured in one run, the two-reader run had two of its three findings from history, and a later single-reader run returned zero candidates and shipped "no issues". The prior-discussion reviewer still runs only when comments exist. Budget the code reviewer at ≤ 30 calls in ≤ 10 messages; verification and the report are unchanged. That turns a 700-line review into a seven-to-nine minute job without giving up the history lens.

**Label for rigour, not for risk-aversion.** The category on a finding decides what happens to it: `bug` and `guidelines` go to an independent verifier, `maintainability` is self-checked, and `comment-contract` is verified only when it is wording rather than behaviour. An author who writes "read this on its own connection, before taking the locks" has written a behavioural invariant; breaking it is a `bug`.

### Efficiency block

Every reviewer prompt carries this block, with the paths filled in — verbatim otherwise. A reviewer should read its whole chunk in one or two calls and spend the rest of its budget reasoning.

```
## Budget
- Your reading pack is at <paths>. It holds every changed file with the scope around its hunks, numbered; `+` marks an added line. Each file ends with IMPORTERS — the files that import it — which is where a contract change breaks. Read the pack in 1-2 calls, one call per part, and re-read only the lines a finding cites. A reader that keeps touring the tree burns the wave's clock: measured in one run, the slowest reader made 61 calls over a 1,000-line pack and took 445 s where the median was 310. Budget: ≤ 40 calls in ≤ 10 messages. (On a small diff, read <diff path> instead — same rule.)
- TEST sections are assertion checks: does the assertion match the new behaviour? Do not read test files beyond their section, and do not report missing coverage.
- Do not run `git diff`, `gh pr diff`, or `sed -n` on repository files, and do not slice files: a slice hides the context that turns a suspicion into a finding.
- Open a repository file only when the pack was cut off or a hunk depends on a caller the pack does not contain — one whole `read`, never a window.
- About 10 messages of tool use, at most ~40 calls. Spend the rest reasoning.
- Finish every `DEEP:` area yourself: open the importer or caller it names, one whole `read`, and decide. Do not open twenty files on spec; if a question needs a runtime observation or a test run, say so in one `DEEP: <path:line> — what would settle it` line and leave it.
```

At `low` effort the prompt omits the pack: pass the chunk diff, say "read the diff; open a file only when a hunk's meaning depends on code outside it", and set the budget to 5 messages / 20 calls.

Each reviewer returns at most 5 findings:

```
- file: <path>
  line: <line or range>
  category: bug | guidelines | history | prior-review | comment-contract | maintainability
  summary: <one sentence: the defect and its impact>
  evidence: <the exact code, command output, or quoted rule that proves it>
  rule: <path + quoted rule, for guidelines findings only>
```

Then one line per area you could not settle: `DEEP: <path:line> — <what context would settle it>`, or `DEEP: none`.

Then at most **five one-line bullets** summarising what you settled, so the orchestrator can cite it — not an essay. Measured in one run: two readers that found nothing wrote ~900 words each of point-by-point justification; that prose is output tokens and output tokens are wall clock.

**A `DEEP:` line is for what the pack cannot answer, not for what you did not read.** Finish every area the pack does contain — the enclosing scope and IMPORTERS are right there — and punt only what needs a runtime observation, a test run, or code outside the change. Measured in one run: 26 `DEEP:` items came back with 16 still unresolved at the end, which turns the reader's budget into the report's homework.

**When the budget runs out, conclude.** At ~40 calls (30 on a small diff) stop exploring and return what you have: findings and `DEEP:` lines for what you could not settle. A reader that overshoots to 48 calls and still writes a long closing spends the wave's clock twice.

"No findings." is a valid answer; "No findings. DEEP: none" is better, because it tells the orchestrator not to spend a second wave.

### What happens to `DEEP:` items

A `DEEP:` line is a note to the reviewer, not a work order for another agent. The reviewer opens the file or caller it names (one whole `read`), decides, and either turns the item into a finding or drops it with a reason. Anything that still cannot be settled — because it needs a runtime observation, a test run, or code outside this change — goes into the report's `未解决` list, one line each: `path:line — what would settle it`.

Count them before the report and reconcile every one: the reviewers' closings name N `DEEP:` items, so the report must contain N entries — a finding, or a `未解决` line. A wave of eight reviewers raises about a dozen; a report that lists two and says nothing about the other ten did not cover them. Measured on a 92-file PR: thirteen `DEEP:` items came back, two reached `未解决`, and the three that an independent reviewer later rated 中 (a per-turn full download of every generated file) or 低 (an N+1 in a retrieval path, an `EXISTS` added to a list query) had all been sitting in `DEEP:`.

### Large waves: the cap is a host setting, not something `workflow` lifts

`maxActiveSubagents` (Settings → Subagent → Limits, default **8**) caps live children per subagent tree, and admission is **reject, not queue**: at capacity a new child fails with `ACTIVATION_LIMIT_REACHED`. `workflow` does not lift that cap — its `agent()` admits through the same pool, and because a failed start throws, **one rejected child kills the whole workflow run**. That is not hypothetical: a ten-chunk review started as a workflow with `maxConcurrentAgents` above eight and died one second in with `WorkflowError: agent() could not start a child: Error: subagent limit reached`; the retry had to batch itself back down to eight, which bought nothing over the plain `subagent` path except the loss of the `reasoning_effort` override.

So the default path is `subagent`. Two ways to go wider:

- Ask the user to raise `maxActiveSubagents` (it applies live), then size the wave to that number. Remember that concurrent agents share model throughput: twelve readers at once finish later per reader than eight.
- Use `workflow` only when the host cap has been raised well above the wave size and a single `subagent` message could not hold it. Keep `maxConcurrentAgents` at or below the cap, pass `provider`/`model` (there is no reasoning-effort override), and accept that the wave then runs at the model's default effort.

```js
// only when maxActiveSubagents >= the wave size; keep the ceiling at or below it
const packs = ['01', '02', '03', '04', '05-1', '05-2']   // whatever packs.md lists
const schema = { type: 'object', required: ['candidates'], properties: {
  candidates: { type: 'array', items: { type: 'object', required: ['file', 'line', 'category', 'summary', 'evidence'],
    properties: { file: { type: 'string' }, line: { type: 'string' }, category: { type: 'string' },
                  summary: { type: 'string' }, evidence: { type: 'string' }, rule: { type: 'string' } } } },
  deep: { type: 'array', items: { type: 'string' } } } }
// the verifier returns a different shape: give it its own schema or every call fails validation
const vSchema = { type: 'object', required: ['verified'], properties: {
  verified: { type: 'array', items: { type: 'object',
    required: ['candidate', 'score', 'verdict', 'severity', 'evidence'],
    properties: { candidate: { type: 'string' }, score: { type: 'number' },
                  verdict: { type: 'string', enum: ['confirmed', 'rejected', 'cannot-verify'] },
                  severity: { type: 'string', enum: ['high', 'medium', 'low'] },
                  evidence: { type: 'string' }, location: { type: 'string' } } } } } }
return await pipeline(packs,
  async (id) => agent(`<reviewer prompt with pack ${id}, budget block, bar, false positives>`, 
    { label: `review-${id}`, phase: 'review', schema }),
  async (review) => review && review.candidates.length
    ? { reviewed: review, verified: await agent(`<verifier prompt for ${JSON.stringify(review.candidates)}>`,
        { label: 'verify', phase: 'verify', schema: vSchema }) }
    : { reviewed: review, verified: { verified: [] } })
```

Return the array; dedupe by `path:line` and root cause before the report, since chunks no longer see each other.

## Step 5 — verify and score every candidate

Deduplicate by root cause and by location, **across readers** — a second reader often re-finds the first one's issue in different words. If more than 12 candidates remain, keep the 12 with the highest impact and say how many were dropped. This cap is what keeps the verification wave short: measured in one run, 23 candidates went to eight verifiers and the wave took 7.3 minutes of a 20.6-minute review.

Route each survivor by category before spending a verifier on it:

- **bug, guidelines, history, prior-review** — any of these can block a merge, so each gets an independent verifier.
- **comment-contract** — verify it unless it is *pure wording*. A comment that states an invariant about locks, ordering, transactions, ownership, or data flow, and that this change now violates, is a structural finding wearing a comment's clothes: the comment is the evidence, not the defect. Verifiers are cheap (60–180 s, in parallel); mis-routing one here is how a merge-blocking hazard comes out as a docs nit.
- **maintainability** — not merge-blocking on its own: `低` unless the duplication or wasted work sits on a hot path the change introduces. Check it yourself: one re-read of the cited lines and the quoted rule, then confirm or drop. A verifier adds nothing to a duplicated helper.

Measured cost of over-routing: on a 92-file PR, eight candidates came back, six were labelled `comment-contract` and self-checked, and the one structural hazard among them — a second pool connection acquired inside a transaction that already held three row locks, against the file's own comment forbidding exactly that — was also filed as `comment-contract`. The report came out with zero high-severity findings.

At `medium` effort and above, **verify once, in one message, when the wave is done.** Collect each reviewer's candidates as it reports, then launch every verifier in a single message as soon as the last reviewer has answered (or the wave deadline fired). This is not a trade against wall-clock time: the last candidate arrives when the slowest reviewer finishes, so a verifier launched earlier only runs alongside a wave that is already ending — measured in one run, the first verifiers started 2.5 minutes before the wave ended and the review still finished at 13.7 minutes, because the last candidate's verifier was the one setting the tail. Batching instead spends two fewer model turns on spawning and makes the tail one verifier long.

The one exception: if a candidate already looks 高 — data loss, a lock or transaction wrong, a security exposure — verify it immediately rather than waiting, because it can change the verdict and the reader may be the straggler.

Run **exactly one verification round**: never re-verify a candidate that already came back.

**Batch the verifiers — one message, at most eight agents.** A verifier started per candidate, one assistant message each, turns the tail into a trickle: measured across sixteen runs, the median spread between the first and last verifier spawn was 106 s, and one 14.9-minute run spawned eighteen verifiers over five messages spanning 366 s — every spawn is a model turn plus an admission, and the later ones wait for a slot. So: collect the candidates that are pending, group them **two to three per verifier**, and launch the whole group in a single message. If you find yourself sending a second verifier message, you launched the first too early: measured in one run, six verifiers went out over five messages spanning 100 s, and the last of them set the tail of an 11.1-minute review. Never exceed eight verifier agents for a review; a verifier may hold up to four candidates if more arrive. There is one batch: launch it once, when the wave is done.

**At `low` effort there is no verifier wave.** Dedupe, then check every candidate yourself with one re-read of the cited lines: keep what you can confirm from the code, drop what you cannot, and label the report `验证状态：未独立验证（low triage）`. Speed is the point of `low`; a candidate you judged high or medium still drives the merge verdict, and the label is what keeps that verdict honest.

**Judging is cheaper than finding.** The session's own model is a fine verifier; a stronger model is a legitimate upgrade for `high`/`max`. Measured on the same candidate: the default model confirmed at 82 in 107 s (29 calls), the stronger one at 88 in 145 s (14 calls) — 35% slower, not the 2.5x it cost as a discoverer, because a verifier answers four bounded questions instead of exploring a chunk. Pin `reasoning_effort` explicitly when you switch the model.

Each verifier gets the reading pack path(s) — or the diff path, on a small diff — its candidate finding, the intent summary, the applicable guidelines, the rubric below, the exact return format including the `severity` field, and a verifier budget (≤ 15 tool calls, ≤ 5 messages). **Tell it to read the cited lines and the enclosing function, not the whole file**: measured in one run, eight verifiers with 2–3 candidates each spent 20–48 calls and 152–439 s, making the verification wave 7.3 minutes — as long as the reviewer wave it followed. **At most two candidates per verifier.** It must re-read the code — the pack gives it the scope; a repository `read` is allowed when the pack was cut off — and decide whether the issue survives, not merely agree.

A verifier holding several candidates returns one block per candidate and judges each on its own evidence, so it may reject one while confirming another; it still re-reads the code for each. Grouping trades a little depth for a wave that finishes in one batch.

Score each issue 0–100 for **confidence that the defect is real** — never for severity, rarity, or impact. Severity is a separate field the verifier fills in below. Use this rubric verbatim:

- **0** — Not confident at all. A false positive that does not stand up to light scrutiny, or a pre-existing issue.
- **25** — Somewhat confident. Might be real, might be a false positive; the failure path could not be confirmed.
- **50** — Moderately confident. The code has the shape of the defect and the verifier believes it is real, but the failure path or the violated contract could not be pinned down.
- **75** — Highly confident. The verifier re-read the code and confirmed the defect exists and that this change caused it — **even if it is rare, narrow, or low-impact**.
- **100** — Absolutely certain. Confirmed with a concrete, step-by-step failure path, or a wrong result the code plainly produces.

⚠ **Rarity is not doubt.** A defect that needs an unlikely input, a sub-second race window, or a failed network call is still a confirmed defect: score it 75–100 and then assign severity from the table below — never lower the score for it. A comment, docstring, or type contract this change made false is likewise confirmed at 75. Lower the score only when you doubt that the defect is real, never because it is minor.

For a `guidelines` finding the verifier must also confirm that the quoted rule's scope covers this file and that the code actually violates it. For a bug it must confirm the failure path, not just the suspicious shape. For a candidate whose evidence is a comment stating a lock, ordering, transaction or ownership invariant: confirm the invariant, then judge severity by the hazard — a second connection inside a transaction, or a lock held across an unrelated await, is 高 — not by how hard the failure is to trigger.

Each verifier returns:

```
candidate: <file:line, as given to you>
score: <0-100>
verdict: confirmed | rejected | cannot-verify
severity: high | medium | low
evidence: <what it re-read and why the score follows>
location: <corrected path:line, when the candidate's location was off>
```

`severity` is assigned from three tiers, and it is the verifier's judgment of the defect — never a restatement of the score:

**Structural hazards are 高 even when they need load to manifest.** A second database connection acquired inside a transaction, a lock taken in the opposite order to another path, a row lock held across an unrelated await, an unbounded retry inside a locked section, or a read the file's own comment says must happen before the locks — these are production-stability defects, not narrow races. When the change has to falsify an invariant comment to be wrong, quote that comment in the evidence and keep the tier at 高: the comment is the specification the author wrote against.

- **高 / high** — the change is wrong for a real user as it stands: a wrong result, data loss or corruption, a security or privacy exposure, a crash, a feature made unreachable, a regression of a prior fix, a plainly violated written guideline, or a structural hazard that can stall production (a second connection acquired while row locks are held, a lock-order inversion, an unbounded retry inside a locked section).
- **中 / medium** — a user-visible wrong behaviour that needs a specific condition, or data that no longer reaches a consumer: a lost update when two requests race, a mode that hands downstream nothing, a message that misstates the state, a regression under a documented setting; or a hot-path cost the change introduces (a per-turn download of every blob, a new scan on a query that runs on every page load). Medium findings do **not** block a merge on their own, but the verdict becomes **修复后合并** — they must be fixed inside this change.
- **低 / low** — convention drift (a migration style the repo's docs argue against), a cold-path N+1 or redundant query with small measured cost, duplicated logic, a stale comment or docstring, naming.

A comment, docstring, or type contract this change made false is confirmed at 75 like any other finding; its tier depends on what it hides — a wording nit is 低, a violated lock/ordering/ownership invariant is 高.

Filter out only what is not a finding: `rejected` and `cannot-verify` verdicts, and confirmed issues scoring below 75. **A confirmed finding is never discarded for being minor** — a 低 is still reported, in the 低 section.

## Step 6 — report

Report **every** surviving finding, highest severity first: 高 before 中 before 低, and within a tier sort by category — correctness bugs, guideline violations, history/contract issues, efficiency, maintainability. Keep at most the requested number (default **32**; `all` keeps every verified finding). **Write the report in Chinese by default.** Use another language only when the user explicitly asks for it.

### 合并结论

Every review of something that can land opens with one of these verdicts, followed by the severity counts behind it:

- **✅ 可以合并** — 0 高, 0 中. 低 findings are still listed and the line names their count.
- **⚠️ 修复后合并** — 0 高, at least one 中. Nothing catastrophic, but every 中 is a user-visible wrong behaviour or a hot-path cost that must be fixed inside this change.
- **⛔ 合并前必须修复** — at least one 高.
- **🚫 不建议合并** — a 高 is structural (design, scope, public contract, migration safety), or a 高 raised in an earlier review is still unresolved.
- **❔ 无法判断** — coverage is incomplete; say what is missing.

A closed or draft PR stops earlier and gets no verdict. For a path target, scope the verdict to that path and say the rest of the branch was not reviewed. Severity is the verifier's, and the report never re-grades it to read better: a 中 whose fix is deferred is still **修复后合并**.

Right after the verdict, state what it rests on: `验证状态：独立验证（N 条候选全部经 verifier）`, `验证状态：部分独立验证（N 条经 verifier，M 条由我自己复核）`, `验证状态：未独立验证（M 条候选由我自己复核 / low triage）`, or — when the readers returned no candidates at all — `验证状态：无候选（N 位读者未发现问题）`. Never call it 独立验证 when no verifier ran. Add one line that build, tests, runtime behaviour and approvals were not checked. The verdict is the review's opinion only — never a CI result, a security sign-off, or a product decision.

### Format (PR/MR target)

```
## 代码评审：PR #<n> <title>

**合并结论：⚠️ 修复后合并** — 0 个高严重度，3 个中严重度，2 个低严重度。
验证状态：部分独立验证（7 条候选：3 条经 verifier，4 条由我自己复核）。
仅代表本次审查：未检查构建、测试、运行时行为和审批状态。

**改动概要**
- 三到五条，说清改了什么、影响谁。

**🔴 高严重度（0）**

无

**🟡 中严重度（3）**

1. **[correctness]** [src/auth/token.ts:88](src/auth/token.ts#L88) — Token 刷新没有清理旧定时器。
   - 影响：每次刷新泄漏一个 interval，并重复发起请求。
   - 证据：`setInterval` 被直接替换，没有 `clearInterval`；`finally` 只清了请求。
   - 建议：替换前先 `clearInterval`。

**🟢 低严重度（2）**

4. **[guidelines]** [src/api/routes.ts:41](src/api/routes.ts#L41) — 新接口无法被访问。
   - 规则：`AGENTS.md` — “Every route file is imported in `routes.ts`.”
   - 证据：新的路由文件没有被导入。
   - 建议：补上导入。

**未解决**
- [src/util/merge.ts:120](src/util/merge.ts#L120) — 需要一次特定数据形态的渲染 fixture 才能确认。

**审查范围**
- 目标：PR #<n>（`main` ← `<branch>`），head `<sha>`
- 变更：92 个文件，+3445 / −229
- 覆盖：6 个分片全部；13 条 `DEEP` 中 11 条已定论
- 未检查：构建、测试、运行时、审批状态

本次评审最多报告 32 条，实际报告 5 条。结论只来自代码阅读，没有跑测试，也没有在浏览器里走查，评审结果没有发到 PR 上。
```

A local branch or revision range uses the same shape with `目标：\`main...feature\`（4 个提交）` instead of the PR line, and the closing sentence without the PR clause.

When nothing survived, still give the verdict line (`合并结论：✅ 可以合并 — 0 个高严重度，0 个中严重度，0 个低严重度`), the 验证状态 line, the 审查范围 section, and then exactly `未发现问题。已检查 bug 与 CLAUDE.md/AGENTS.md 合规性。`

**A single reader is not a clean bill of health.** Before shipping "no issues", make sure the review actually had a second pair of eyes: the history reader counts, a second reader on the single pack counts, a verifier on any candidate counts. If none ran, spawn the history reader (or the contracts-framing second reader) before the verdict — measured in one run, one reader returned zero candidates and the review declared the PR clean, where the earlier two-reader run had found three findings (two from history). If spawning is impossible, the verdict line must say so: `验证状态：未独立验证（单 reviewer，未发现问题）`.

### Rules for the report

- One numbered item per finding, numbered continuously across the three tiers. Sub-bullets: `影响`, `证据`, `建议` (use `规则` instead of `影响` for a guideline finding).
- Link `path:line` relative to the repository root. Never cite a line you did not read.
- The three section markers (🔴/🟡/🟢) are the only emoji; no filler, no restating the whole change beyond 改动概要.
- Every `DEEP:` item the reviewers raised must appear as a finding or under `未解决`; the 覆盖 line states how many of each.
- The closing sentence states the cap, the count reported, what was not checked, and whether the review was posted to the PR.
- The no-issues sentence exists only for a review that confirmed nothing at all.

### Applying fixes is a separate task

The review never applies its own findings. Only when the user explicitly asks after reading the report, treat applying them as a separate coding task with its own verification. A `--fix` argument is ignored during review; say that the review is read-only and does not modify code.

### `--comment`

Only when the target is a pull request. Post one comment, never one per finding, and never a duplicate:

```bash
gh pr comment <number> --body-file <file>
```

Write the comment body to a temporary file, post it, then delete the file. Use the same list format as the report, starting with the merge verdict and its review-only caveat, with the 高/中/低 sections, but link code with the full head SHA:

```
https://github.com/<owner>/<repo>/blob/<full-sha>/<path>#L<start>-L<end>
```

Include at least one line of context around the cited range. For GitLab, post the same body with
`glab mr note <id> --message "$(cat <file>)"`. Do not post when nothing survived verification unless
the user asked for a clean-review comment. Do not post inline suggestions that only partially fix an
issue.

## False positives — do not flag

- Pre-existing issues, or real issues on lines the change did not modify.
- Something that looks wrong but is correct in context.
- Pedantic nitpicks a senior engineer would not raise: naming, formatting, wording, and taste. A comment, docstring, or type contract **this change made false** is not a nitpick — report it at `低` unless it hides a behavioural change.
- Anything a linter, typechecker, or compiler will catch (missing imports, type errors, formatting).
- General quality concerns (test coverage, missing documentation, broad security advice) unless a written guideline explicitly requires them.
- A guideline violation the code explicitly silences (for example a lint-ignore comment).
- Behavior that is intentional or directly required by the change's stated purpose. **Its unstated side effects are still in scope**: if the PR says "deleting a user's data deletes their row in a shared record", that deletion is deliberate, but resetting the row's version — which lets another member's stale page silently overwrite what someone else just saved — is a separate consequence and is judged on its own.
- Speculation that depends on unknown inputs, deployment state, or a future change.

## Stop conditions

Stop and say so plainly when: the target does not exist; the diff is empty; `gh`/`glab` is required but unavailable; the PR is closed, draft, or already reviewed and unchanged. When no candidate survives verification, report the clean result instead of stopping silently. Never pad a review with unverified findings to look thorough; a **confirmed** defect that happens to be minor is not padding and belongs in the report.
