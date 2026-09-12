#!/usr/bin/env bash
# Run the eval harness after any edit to src/*.py.
#
# Why: the compression rules and the answer-quality checks live in the same
# repo, and it is too easy to change a rule and *say* answers still hold
# without proving it. This makes the proof automatic — edit a rule, the
# harness runs, and a regression is reported back before anything else.
#
# Exit 2 is a blocking error for a PostToolUse hook: stderr goes back to
# Claude, so a broken rule cannot be quietly ignored.

set -uo pipefail

# Not hardcoded. This file used to open with an absolute path under one
# machine's home directory, which made the hook a silent no-op in every other
# clone — the `case` below would match nothing and fall through to `exit 0`,
# reporting green while guarding nothing. That is Rule 12's failure exactly, and
# it survived here because the one machine it worked on was the only one anyone
# ran it from.
#
# $CLAUDE_PROJECT_DIR is what .claude/settings.json already uses to invoke this
# script, so it is set whenever Claude Code is the caller. The git fallback is
# for running it by hand. If neither resolves, say so — do not skip quietly.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
if [ -z "$PROJECT_DIR" ] || [ ! -d "$PROJECT_DIR/src" ]; then
  echo "eval harness SKIPPED: cannot locate the repo (CLAUDE_PROJECT_DIR unset and not inside a git work tree)" >&2
  exit 0
fi
PYTHON="$PROJECT_DIR/.venv/bin/python"

# Every sample present, found by globbing rather than listed. A hook that only
# guarded one payload would miss a regression in the other, which is the whole
# reason a second one exists — and a hardcoded list means a payload added later
# is silently never checked. nullglob so an empty data/samples/ yields nothing
# rather than the literal pattern.
shopt -s nullglob
SAMPLES=("$PROJECT_DIR"/data/samples/*.json)

# Announced, not assumed. Without jq the substitution below yields an empty
# file_path, the case falls through to *) exit 0, and this hook becomes a silent
# no-op — reporting nothing while src/*.py is edited unguarded. Every other skip
# in this script says so out loud; this one used to be the exception.
if ! command -v jq >/dev/null 2>&1; then
  echo "eval harness SKIPPED: jq not found — this hook cannot read its input" >&2
  exit 0
fi

file_path=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')

# Only care about Python files under this project's src/, plus the launcher.
#
# bin/margin is not Python and not under src/, but it holds real logic — the
# symlink-resolution loop and the venv path — and breaking it breaks the tool
# just as thoroughly as breaking cli.py. Without this line, the one file whose
# whole job is "find the repo from wherever you were invoked" was the one file
# no gate ever ran on.
case "$file_path" in
  "$PROJECT_DIR"/src/*.py) ;;
  "$PROJECT_DIR"/bin/margin) ;;
  *) exit 0 ;;
esac

# The sample payload is gitignored, and the venv is local — if either is
# missing this is a fresh checkout, not a regression. Say so, don't fail.
[ -x "$PYTHON" ] || { echo "eval harness skipped: no .venv (run: uv venv .venv)"; exit 0; }

# Runs first, and without a sample payload: it generates its own, so it is the
# one gate that still works on a fresh checkout where the samples are absent.
# It also asks a different question from the harness — the harness asks whether
# two known payloads keep their answers, this asks whether the format survives
# shapes nobody thought to write down.
if ! output=$("$PYTHON" "$PROJECT_DIR/src/property_test.py" 2>&1); then
  echo "property test FAILED after editing $file_path" >&2
  echo "$output" >&2
  exit 2
fi

# Same reasoning, one layer up: it generates its own payload, so it runs on a
# fresh checkout too. It asks the question neither other gate can — both of
# those call compress_json in-process, so no argument, stream or exit code was
# ever checked by anything until this existed.
if ! output=$("$PYTHON" "$PROJECT_DIR/src/cli_test.py" 2>&1); then
  echo "CLI contract FAILED after editing $file_path" >&2
  echo "$output" >&2
  exit 2
fi

# mutation_test.py deliberately does NOT run here. It ran here until 2026-09-11,
# and it was ~45 of this chain's ~62 seconds — roughly three quarters of the cost
# of every single edit to any src/*.py file. Without it this chain measures 17.0s
# end to end (2026-09-11, timed through this script). Treat all of these as ±10%:
# two runs of mutation_test the same afternoon gave 43.6s and 46.1s, which is why
# the decision rests on the ratio and not on the second decimal.
#
# It is not skipped, and this is not Rule 12's hole: .githooks/pre-commit runs it
# on every commit, against the staged tree. It moved from per-edit to per-commit,
# which is the cadence its question actually has. "Can the other gates still
# fail?" is a property of the gates, not of the edit in front of you — it changes
# when a check is rewritten, not when a threshold moves — and nothing can reach
# main without passing it (Rule 13 holds: it is still automated, not intended).
#
# The reason to care about the seconds: a gate that makes the loop painful is a
# gate that gets bypassed, and a bypassed gate reports green while guarding
# nothing. That is the same failure this file exists to prevent, one level up.

# The third process boundary. cli_test covers argv and streams; this covers
# JSON-RPC over a pipe, which is the only place the MCP protocol exists.
if ! output=$("$PYTHON" "$PROJECT_DIR/src/mcp_test.py" 2>&1); then
  echo "MCP contract FAILED after editing $file_path" >&2
  echo "$output" >&2
  exit 2
fi

# Milliseconds, no network, no key, no payload. It asks whether eval_model.py's
# verdict can still refuse — three review findings in one round were the same
# vacuous-pass bug, two of them written after Rule 16 was added for exactly that.
if ! output=$("$PYTHON" "$PROJECT_DIR/src/eval_model.py" --self-test 2>&1); then
  echo "eval_model verdict self-test FAILED after editing $file_path" >&2
  echo "$output" >&2
  exit 2
fi

ran=0
for sample in "${SAMPLES[@]}"; do
  # Samples are gitignored real API payloads, so a fresh checkout has none.
  # Missing is not a regression.
  [ -f "$sample" ] || continue
  ran=$((ran + 1))

  output=$("$PYTHON" "$PROJECT_DIR/src/eval_harness.py" "$sample" 2>&1)
  status=$?
  # 3 = no questions written for this payload yet. Unfinished, not broken.
  if [ "$status" -eq 3 ]; then
    echo "note: $(basename "$sample") has no eval questions yet — unguarded"
    ran=$((ran - 1))
  elif [ "$status" -ne 0 ]; then
    echo "eval harness FAILED on $(basename "$sample") after editing $file_path" >&2
    echo "$output" >&2
    exit 2
  fi
done

[ "$ran" -gt 0 ] || { echo "eval harness skipped: no sample payloads present"; exit 0; }
echo "eval harness: all checks pass on $ran sample(s)"
exit 0
