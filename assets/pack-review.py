#!/usr/bin/env python3
"""Build one reading pack per diff chunk: the changed code inside the code that surrounds it.

Why: a reviewer that has to hunt — `sed -n` here, `read` a 200-line window there — spends its
turns on navigation and stops when the budget runs out. A reviewer handed one file containing
the hunk *and the enclosing scope* reads the whole chunk in 1-3 calls and reasons about real
context. Measured: a 150-file review whose reviewers sliced files returned 5 candidates; the
same PR read file-by-file returned 15.

Usage:
    python3 pack-review.py <full.diff> <worktree> <chunks-dir> <out-dir> \
        [--context 40] [--max-file-lines 320] [--max-pack-lines 2200]

Writes pack-NN.md per chunk-NN.diff plus packs.md, and prints that summary.

Reading rules baked into each pack:
- source files up to --max-file-lines are included whole; larger files contribute the enclosing
  scope of every hunk plus the file head (imports, types);
- added lines are marked `+` in the numbered excerpt, so the change is visible without a second
  copy of the diff;
- test files contribute their raw hunks only — a test is checked for assertions that contradict
  the new behaviour, not read as if it were the feature;
- every changed module also carries its **importers** — the files that import it, with the import
  line — so "who calls this" costs no exploration. That cross-file hop is where the measured
  blocking findings came from;
- a pack that would exceed --max-pack-lines is truncated with an explicit marker and reported in
  packs.md, so the reviewer knows what it did not see.
"""

import argparse
import os
import re
import subprocess
import sys

FILE_HEAD = re.compile(r'^diff --git a/(\S+) b/(\S+)')
HUNK_HEAD = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')
SCOPE_START = re.compile(
    r'^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|interface|type|const|let|var|'
    r'def|func|fn|impl|struct|enum)\b'
)
TEST_PATH = re.compile(r'\.(?:test|spec)\.[cm]?[jt]sx?$|(?:^|/)__tests__/|(?:^|/)tests?/')

HEAD_LINES = 30
IMPORT_LINE = re.compile(
    r"""(?:from\s+['"]([^'"]+)['"]"""          # JS/TS: from '…'
    r"""|require\(\s*['"]([^'"]+)['"]"""      # JS/TS: require('…')
    r"""|import\s+['"]([^'"]+)['"]"""          # JS/TS side-effect, Go: import "…"
    r"""|from\s+([\w.]+)\s+import"""         # Python: from pkg.mod import …
    r"""|import\s+([\w.]+))"""                # Python / Java / Kotlin: import pkg.Mod
)


def importer_index(worktree, paths, max_importers):
    """Map changed module stem -> [(path, line, text)] for files importing it."""
    stems = {}
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        stems.setdefault(stem, []).append(path)
    if not stems:
        return {}
    # POSIX ERE (git grep -E): no \s, no (?:...) — BSD grep on macOS rejects both
    alts = '|'.join(re.escape(s) for s in sorted(stems))
    pattern = ("(from[[:space:]]+['\"][^'\"]*(%s)['\"]"
               "|require[(][[:space:]]*['\"][^'\"]*(%s)['\"]"
               "|import[[:space:]]+['\"][^'\"]*(%s)['\"]"
               "|(from|import)[[:space:]]+[A-Za-z0-9_.]*(%s))" % (alts, alts, alts, alts))
    try:
        out = subprocess.run(['git', '-C', worktree, 'grep', '-n', '-I', '-E', pattern],
                             capture_output=True, text=True, timeout=180).stdout
    except Exception:
        return {}
    found = {}
    for row in out.splitlines():
        parts = row.split(':', 2)
        if len(parts) != 3:
            continue
        path, line, text = parts
        m = IMPORT_LINE.search(text)
        if not m:
            continue
        spec = next(g for g in m.groups() if g)
        hit = None
        for stem, owners in stems.items():
            if re.search(r'(?:^|[./])%s$' % re.escape(stem), spec):
                hit = stem
                break
        if hit is None:
            continue
        bucket = found.setdefault(hit, [])
        if len(bucket) < max_importers and all(p != path for p, _, _ in bucket):
            bucket.append((path, int(line), text.strip()))
    return found


def parse_files(text):
    """Return [(path, part)] in diff order."""
    out = []
    for part in re.split(r'(?m)^(?=diff --git )', text):
        m = FILE_HEAD.match(part) if part.startswith('diff --git ') else None
        if m:
            out.append((m.group(2), part))
    return out


def scan_hunks(part):
    """Return [(new_start, [(line_no, text)], raw_text)] for each hunk."""
    lines = part.splitlines(keepends=True)
    starts = [i for i, l in enumerate(lines) if l.startswith('@@ ')]
    blocks = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(lines)
        m = HUNK_HEAD.match(lines[start])
        new_start, added = int(m.group(1)) if m else 1, []
        cursor = new_start
        for line in lines[start + 1:end]:
            if line.startswith('+'):
                added.append((cursor, line[1:].rstrip()))
                cursor += 1
            elif line.startswith(' '):
                cursor += 1
        blocks.append((new_start, added, ''.join(lines[start:end])))
    return blocks


def matches_head(src, added):
    """True when the head file really has the diff's added lines at the diff's numbers."""
    return all(0 < n <= len(src) and src[n - 1].rstrip() == text for n, text in added)


def scope_start(lines, hunk_line, context):
    ceiling = max(1, hunk_line - 200)
    for n in range(hunk_line - 1, ceiling - 1, -1):
        if 0 <= n - 1 < len(lines) and SCOPE_START.match(lines[n - 1]):
            return n
    return max(1, hunk_line - context)


def numbered(lines, first, last, added):
    out = []
    for n in range(max(1, first), min(len(lines), last) + 1):
        mark = '+' if n in added else ' '
        out.append(f'{n:>5} {mark}{lines[n - 1].rstrip()}')
    return out


def build_pack(chunk_path, worktree, args, importers=None):
    entries = parse_files(open(chunk_path, errors='replace').read())
    entries_by_path = {p for p, _ in entries}
    blocks, changed, tests = [], 0, 0
    for path, part in entries:
        is_test = bool(TEST_PATH.search(path))
        tests += is_test
        hunks = scan_hunks(part)
        changed += len(part.splitlines())
        blocks += ['', f'### {path} — {"TEST" if is_test else "SOURCE"}']

        raw = '\n'.join(h[2].rstrip('\n') for h in hunks)
        full = os.path.join(worktree, path)
        if is_test or not hunks or not os.path.exists(full):
            blocks += ['```diff', raw, '```']
            continue

        src = open(full, errors='replace').read().splitlines()
        if not all(matches_head(src, nums) for _, nums, _ in hunks if nums):
            # the checkout is not at the diff's head: show the hunks instead of a lying excerpt
            blocks += ['(worktree copy does not match this diff — raw hunks)', '```diff', raw, '```']
            continue
        added = {n for _, nums, _ in hunks for n, _ in nums}
        spans = []
        if len(src) <= args.max_file_lines:
            spans.append((1, len(src)))
        else:
            spans.append((1, min(HEAD_LINES, len(src))))
            for hunk_start, _, _ in hunks:
                spans.append((scope_start(src, hunk_start, args.context), hunk_start + args.context))
        merged = []
        for start, end in spans:
            if merged and start <= merged[-1][1] + 3:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        blocks.append('```text')
        for start, end in merged:
            blocks += numbered(src, start, end, added)
            if end < len(src):
                blocks.append(f'   ... ({len(src) - end} more lines)')
        blocks.append('```')
        removed = sum(1 for line in part.splitlines() if line.startswith('-') and not line.startswith('--- '))
        if removed:
            blocks.append(f'({removed} lines removed in this file — `-` lines are not shown)')

        stem = os.path.splitext(os.path.basename(path))[0]
        hits = (importers or {}).get(stem, [])
        if hits:
            blocks.append('IMPORTERS (files that import this module — read one when the hunk changes its contract):')
            for ipath, iline, itext in hits:
                known = ' (in this pack)' if ipath in entries_by_path else ''
                blocks.append(f'- {ipath}:{iline}{known} — {itext[:160]}')
    return blocks, len(entries), changed, tests


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('diff')
    ap.add_argument('worktree')
    ap.add_argument('chunks_dir')
    ap.add_argument('out_dir')
    ap.add_argument('--context', type=int, default=40)
    ap.add_argument('--max-file-lines', type=int, default=320)
    ap.add_argument('--max-pack-lines', type=int, default=2200)
    ap.add_argument('--max-importers', type=int, default=6,
                    help='importers listed per changed module (0 disables the index)')
    args = ap.parse_args(argv)

    chunks = sorted((f for f in os.listdir(args.chunks_dir) if re.fullmatch(r'chunk-\d+\.diff', f)),
                    key=lambda f: int(re.search(r'\d+', f).group()))
    if not chunks:
        sys.exit(f'no chunk-NN.diff files in {args.chunks_dir}')

    os.makedirs(args.out_dir, exist_ok=True)
    lines = ['# reading packs', '']

    importers = {}
    if args.max_importers:
        changed = [p for name in chunks
                   for p, _ in parse_files(open(os.path.join(args.chunks_dir, name),
                                                errors='replace').read())]
        importers = importer_index(args.worktree, changed, args.max_importers)
        lines.append(f'# importer index: {sum(len(v) for v in importers.values())} cross-file '
                     f'import lines over {len(importers)} modules')
        lines.append('')
    for name in chunks:
        blocks, nfiles, changed, tests = build_pack(
            os.path.join(args.chunks_dir, name), args.worktree, args, importers)
        # split on file boundaries so a big chunk becomes several complete packs instead of a
        # truncated one: a reviewer that silently loses the tail files reports on half a chunk
        groups, current = [], []
        for block in blocks:
            if block.startswith('### ') and current and len(current) > 40:
                groups.append(current)
                current = []
            current.append(block)
        if current:
            groups.append(current)

        parts, pending = [], []
        for group in groups:
            if pending and sum(len(g) for g in pending) + len(group) > args.max_pack_lines:
                parts.append(pending)
                pending = []
            pending.append(group)
        if pending:
            parts.append(pending)

        stems = []
        for index, part in enumerate(parts, 1):
            suffix = '' if len(parts) == 1 else f'-{index}'
            stem = f'pack-{name[6:8]}{suffix}'
            stems.append(stem)
            head = [
                f'# Reading pack — {name[:-5]}{"" if len(parts) == 1 else f" (part {index}/{len(parts)})"}',
                '',
                f'{nfiles} files ({tests} test), {changed} diff lines. The excerpts are numbered; a `+` marks '
                f'an added line. Cite `path:line` using those numbers. TEST files are for assertion checks only.',
                '',
            ]
            body = head + [b for group in part for b in group]
            out = os.path.join(args.out_dir, f'{stem}.md')
            with open(out, 'w') as fh:
                fh.write('\n'.join(body) + '\n')
        total = sum(len(g) for g in groups)
        lines.append(f'- {" + ".join(stems)} — {nfiles} files ({tests} test), {changed} diff lines, '
                     f'{total} text lines -> {os.path.join(args.out_dir, stems[0])}.md')

    text = '\n'.join(lines)
    open(os.path.join(args.out_dir, 'packs.md'), 'w').write(text + '\n')
    print(text)


if __name__ == '__main__':
    main(sys.argv[1:])
