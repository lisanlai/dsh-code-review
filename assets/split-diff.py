#!/usr/bin/env python3
"""Split a unified diff into balanced chunk files for parallel review.

Why: a reviewer that re-derives its own slice of the diff spends model turns on navigation
instead of review, and every turn is a round trip. Splitting once, up front, lets each
reviewer start reading immediately.

Usage:
    python3 split-diff.py <full.diff> <out-dir> [--chunks N] [--max-lines N] [--max-files N]

Writes <out-dir>/chunk-01.diff … and <out-dir>/manifest.md, and prints the manifest.
Chunks are packed by directory so related files stay together; a directory larger than
the budget is split per file. Lockfiles and generated output are listed, not chunked.

--max-files keeps a chunk small enough for a reviewer to open every changed file whole: a
chunk of 2,000 diff lines spread over 35 files gets a shallower review than the same lines
over 10 files, because the reviewer spends its reading budget on the file list.
"""

import argparse
import collections
import os
import re
import sys

FILE_HEAD = re.compile(r'^diff --git a/(\S+) b/(\S+)')

DEFAULT_MAX_LINES = 1500
MAX_CHUNKS = 16

TEST_PATH = re.compile(r'\.(?:test|spec)\.[cm]?[jt]sx?$|(?:^|/)__tests__/|(?:^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.(go|py)$|(Test|Tests)\.java$')

SKIP = re.compile(
    r'(^|/)(pnpm-lock\.yaml|package-lock\.json|yarn\.lock|Cargo\.lock|go\.sum)$'
    r'|\.min\.(js|css)$'
    r'|(^|/)(dist|build|out)/'
    r'|\.snap$'
)


def parse(text):
    """Return (header, [(path, diff_lines, text)])."""
    parts = [p for p in re.split(r'(?m)^(?=diff --git )', text) if p.startswith('diff --git ')]
    if not parts:
        return text, []
    header = text[: text.index(parts[0])]
    files = []
    for part in parts:
        m = FILE_HEAD.match(part)
        files.append((m.group(2) if m else '(unknown)', len(part.splitlines()), part))
    return header, files


def _split(group, max_lines, max_files):
    """Cut one directory's files into balanced sub-groups that respect both budgets."""
    ordered = sorted(group, key=lambda f: -f[1])
    total = sum(n for _, n, _ in ordered)
    parts = max(1, -(-total // max_lines))
    if max_files:
        parts = max(parts, -(-len(ordered) // max_files))
    subs, loads = [[] for _ in range(parts)], [0] * parts
    for f in ordered:
        i = loads.index(min(loads))
        subs[i].append(f)
        loads[i] += f[1]
    label = os.path.dirname(ordered[0][0]) or '.'
    return [(label, load, sub) for load, sub in zip(loads, subs) if sub]


def pack(files, max_lines, max_files=0):
    """Group by directory, then greedily balance the groups across chunks."""
    total = sum(n for _, n, _ in files)
    count = max(1, -(-total // max_lines))
    if max_files:
        # a file-heavy chunk gets a shallower read, so files can force more chunks
        count = max(count, -(-len(files) // max_files))
    count = max(1, min(MAX_CHUNKS, count))

    by_dir = collections.defaultdict(list)
    for f in files:
        by_dir[os.path.dirname(f[0])].append(f)

    units = []
    for directory, group in by_dir.items():
        size = sum(n for _, n, _ in group)
        too_big = size > max_lines or (max_files and len(group) > max_files)
        if too_big:
            units += _split(group, max_lines, max_files)
        else:
            units.append((directory, size, group))

    chunks, load = [[] for _ in range(count)], [0] * count
    for _, size, group in sorted(units, key=lambda u: -u[1]):
        fits = [i for i in range(count)
                if not max_files or not chunks[i] or len(chunks[i]) + len(group) <= max_files]
        target = min(fits or range(count), key=lambda i: load[i])
        chunks[target].extend(group)
        load[target] += size
    kept = [(c, n) for c, n in zip(chunks, load) if c]
    return [c for c, _ in kept], [n for _, n in kept]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('diff')
    ap.add_argument('out_dir')
    ap.add_argument('--chunks', type=int, help='exact number of chunks (1-8); overrides --max-lines')
    ap.add_argument('--max-lines', type=int, default=DEFAULT_MAX_LINES)
    ap.add_argument('--max-files', type=int, default=0,
                    help='cap files per chunk (0 = only the line budget decides)')
    args = ap.parse_args(argv)

    with open(args.diff, errors='replace') as fh:
        header, files = parse(fh.read())
    if not files:
        sys.exit(f'no file diffs found in {args.diff}')

    kept = [f for f in files if not SKIP.search(f[0])]
    skipped = [f[0] for f in files if SKIP.search(f[0])]
    wanted = min(MAX_CHUNKS, max(1, args.chunks)) if args.chunks else 0
    if wanted:
        args.max_lines = max(1, -(-sum(n for _, n, _ in kept) // wanted))
    chunks, load = pack(kept, args.max_lines, args.max_files)

    os.makedirs(args.out_dir, exist_ok=True)
    lines = [f'# {len(kept)} files, {sum(n for _, n, _ in kept)} diff lines, {len(chunks)} chunks', '']
    for i, chunk in enumerate(chunks, 1):
        path = os.path.join(args.out_dir, f'chunk-{i:02d}.diff')
        with open(path, 'w') as fh:
            fh.write(header)
            for _, _, text in chunk:
                fh.write(text)
        lines.append(f'## chunk-{i:02d} — {load[i-1]} diff lines, {len(chunk)} files -> {path}')
        for name, n, _ in sorted(chunk, key=lambda f: -f[1]):
            lines.append(f'- {name} ({n}){" [test]" if TEST_PATH.search(name) else ""}')
        lines.append('')
    if skipped:
        lines += ['## not chunked (lockfiles, generated)', *[f'- {s}' for s in skipped]]

    # a wave that needs more agents than the harness runs at once should start its heaviest
    # chunks first: the queued tail then costs the least (longest-processing-time-first).
    order = sorted(range(len(chunks)), key=lambda i: -(load[i] + 20 * len(chunks[i])))
    lines += ['', '## launch order (heaviest first)',
              *[f'{k}. chunk-{i+1:02d} — {load[i]} diff lines, {len(chunks[i])} files'
                for k, i in enumerate(order, 1)]]

    manifest = '\n'.join(lines)
    with open(os.path.join(args.out_dir, 'manifest.md'), 'w') as fh:
        fh.write(manifest + '\n')
    print(manifest)


if __name__ == '__main__':
    main(sys.argv[1:])
