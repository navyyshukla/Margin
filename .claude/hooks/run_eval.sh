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
SAMPLE="$PROJECT_DIR/data/samples/github_issues.json"
PYTHON="$PROJECT_DIR/.venv/bin/python"

file_path=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')

# Only care about Python files under this project's src/.
case "$file_path" in
  "$PROJECT_DIR"/src/*.py) ;;
  *) exit 0 ;;
esac

# The sample payload is gitignored, and the venv is local — if either is
# missing this is a fresh checkout, not a regression. Say so, don't fail.
[ -x "$PYTHON" ] || { echo "eval harness skipped: no .venv (run: uv venv .venv)"; exit 0; }
[ -f "$SAMPLE" ] || { echo "eval harness skipped: no data/samples/github_issues.json"; exit 0; }

output=$("$PYTHON" "$PROJECT_DIR/src/eval_harness.py" "$SAMPLE" 2>&1)
status=$?

if [ $status -eq 0 ]; then
  echo "eval harness: all checks pass"
  exit 0
fi

echo "eval harness FAILED after editing $file_path" >&2
echo "$output" >&2
exit 2
