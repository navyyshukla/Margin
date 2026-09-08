#!/usr/bin/env bash
# Install this repo's hooks into .git/hooks/.
#
# Why copy instead of pointing core.hooksPath at .githooks: hooks are files in
# the working tree, so a tracked hooks directory only exists on branches that
# carry the commit adding it. Checking out an older branch (main, before this
# work is merged) would silently remove the hook — including the one whose
# whole job is refusing commits on main. .git/hooks lives outside the working
# tree, so a copy there survives every branch switch.
#
# Cost of the copy: editing .githooks/pre-commit does not take effect until
# this is re-run. Run it after changing a hook.
#
# Usage: ./.githooks/install.sh

set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
src="$repo_root/.githooks"
dest="$repo_root/.git/hooks"

# core.hooksPath would override .git/hooks entirely, so make sure it is unset.
git config --unset core.hooksPath 2>/dev/null || true

for hook in "$src"/*; do
  name=$(basename "$hook")
  [ "$name" = "install.sh" ] && continue
  cp "$hook" "$dest/$name"
  chmod +x "$dest/$name"
  echo "installed: $name"
done
