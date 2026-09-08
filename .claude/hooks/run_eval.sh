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

PROJECT_DIR="/Users/navyshukla/Margin Project"
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
