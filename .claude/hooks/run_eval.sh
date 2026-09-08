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

# Every sample with a check module. A hook that only guarded one payload would
# miss a regression in the other, which is the whole reason a second one exists.
SAMPLES=(
  "$PROJECT_DIR/data/samples/github_issues.json"
  "$PROJECT_DIR/data/samples/hn_stories.json"
)

file_path=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')

# Only care about Python files under this project's src/.
case "$file_path" in
  "$PROJECT_DIR"/src/*.py) ;;
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

ran=0
for sample in "${SAMPLES[@]}"; do
  # Samples are gitignored real API payloads, so a fresh checkout has none.
  # Missing is not a regression.
  [ -f "$sample" ] || continue
  ran=$((ran + 1))

  if ! output=$("$PYTHON" "$PROJECT_DIR/src/eval_harness.py" "$sample" 2>&1); then
    echo "eval harness FAILED on $(basename "$sample") after editing $file_path" >&2
    echo "$output" >&2
    exit 2
  fi
done

[ "$ran" -gt 0 ] || { echo "eval harness skipped: no sample payloads present"; exit 0; }
echo "eval harness: all checks pass on $ran sample(s)"
exit 0
