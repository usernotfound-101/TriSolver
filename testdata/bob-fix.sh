#!/usr/bin/env bash
# Adapter shim: run Bob headless on one TriSolver brief, hand back a patch.
#
#   ./bob-fix.sh <brief.md> <out.patch>
#
# Bob edits the working tree the way an agent naturally would; the diff of
# what it changed becomes the PatchProposal that TriSolver's gates judge.
# Nothing is committed and nothing is pushed -- the tree is restored after.

set -euo pipefail

# Resolve before any cd: both are usually passed relative to the caller's
# directory, and this script changes into the workspace.
BRIEF="$(realpath -m "${1:?usage: bob-fix.sh <brief> <patch>}")"
PATCH="$(realpath -m "${2:?usage: bob-fix.sh <brief> <patch>}")"
mkdir -p "$(dirname "$PATCH")"
WS="$(cd "$(dirname "${BASH_SOURCE[0]}")/storefront" && pwd)"
LOGDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/work/bob-logs"
mkdir -p "$LOGDIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

cd "$WS"
git checkout -q pricing-integer-cents
git reset -q --hard
git clean -qfd

cp "$BRIEF" "$WS/TRISOLVER_TASK.md"
trap 'rm -f "$WS/TRISOLVER_TASK.md"' EXIT

echo "==> bob run (agent mode, subagents enabled) on $(basename "$BRIEF")"
# stdin MUST be /dev/null: headless `bob run` blocks forever waiting on a
# TTY prompt otherwise, producing no output and no log file at all.
# MCP is disabled because this task needs no MCP servers and two configured
# ones are unreachable here, which only slows startup.
bob run \
  --workspace "$WS" \
  --mode agent \
  --max-turns "${BOB_MAX_TURNS:-40}" \
  --max-cost "${BOB_MAX_COST:-2}" \
  --format stream-json \
  --disable-mcp \
  --accept-license \
  "Read TRISOLVER_TASK.md in this workspace and carry out the task it describes. Edit the source files in place. Do not write a patch file yourself and do not commit anything." \
  > "$LOGDIR/bob-$STAMP.jsonl" 2>"$LOGDIR/bob-$STAMP.err" < /dev/null || echo "   (bob exited $?)"

rm -f "$WS/TRISOLVER_TASK.md"
git add -A -N >/dev/null 2>&1 || true
git diff > "$PATCH"

echo "==> transcript: $LOGDIR/bob-$STAMP.jsonl"
echo "==> patch:      $PATCH ($(wc -l < "$PATCH") lines)"
