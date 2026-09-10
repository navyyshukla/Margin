"""Does the CLI gate actually fail when the CLI is broken?

Every other gate here asks whether the code is right. This one asks whether the
*gate* is right, and it exists because that question was answered wrong six
times in one pull request.

Each of those six was the same shape: a check that passed, and went on passing
after the thing it was named for was deleted.

  - "stdout round-trips" passed with the compressor replaced by `cat`, because
    plain JSON decompresses fine.
  - "bin/margin without a .venv" passed with the symlink-resolution loop
    deleted, because it only asserted an exit code and a word in the message.
  - "compress.py entry point behaves the same on a closed pipe" passed with
    compress.py's entire __main__ block deleted, because a silent no-op has no
    traceback either.
  - Both closed-pipe checks passed with SIGPIPE handling removed, because the
    665-byte test document fit in the pipe buffer and the pipe never closed
    under the writer.
  - The pathological-input check stopped covering compression the moment its
    payload got deep enough to die in the parser instead.

Five of the six were found by a code reviewer rather than by me, at roughly
80,000 tokens a round, and three were introduced by the fix for the previous
one. Every single one would have been caught by this file, which runs in three
seconds and costs nothing.

**The lesson is narrower than "test your tests".** Deleting the code under test
is the easy mutation and I ran it by hand each time. The two I missed were the
ones where the FIXTURE quietly stopped reaching the code — a document that got
too small, a payload that got too deep. So a mutation here is not "does the
suite go red", it is "does *the check named for this behaviour* go red", which
is the only form that notices a fixture drifting away from its subject.

Run: python src/mutation_test.py
"""

import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Mutation:
    """One deliberate break, and the check that has to notice it."""

    def __init__(self, name, path, old, new, must_fail, gate="cli_test.py"):
        self.name = name
        self.path = path      # relative to the repo root
        self.old = old        # exact text to replace; must be present
        self.new = new
        self.must_fail = must_fail  # substring of the failure line that must appear
        # Which gate is supposed to catch it. Format-level behaviour is guarded
        # by property_test.py and stream-level behaviour by cli_test.py, so
        # running one file for every mutation reports a hole that is really a
        # mutation pointed at the wrong gate — which is how the two dictionary
        # mutations first came back SURVIVED.
        self.gate = gate


MUTATIONS = [
    Mutation(
        "the compressor is replaced by cat",
        "src/cli.py",
        '        document = text if text.endswith("\\n") else text + "\\n"\n'
        '        sys.stdout.buffer.write(document.encode("utf-8"))',
        "        sys.stdout.buffer.write(raw_bytes)",
        "#margin/v1 document",
    ),
    Mutation(
        "notes are written to stdout instead of stderr",
        "src/cli.py",
        '            print(f"margin: {message}", file=sys.stderr)',
        '            print(f"margin: {message}")',
        "no note leaked into it",
    ),
    Mutation(
        "the unchanged path re-adds a trailing newline",
        "src/cli.py",
        "    if text == raw_text:",
        "    if False:",
        "byte-identical to the input",
    ),
    Mutation(
        "SIGPIPE handling is removed",
        "src/cli.py",
        '    if hasattr(signal, "SIGPIPE"):  # absent on Windows; this is a macOS tool\n'
        "        signal.signal(signal.SIGPIPE, signal.SIG_DFL)",
        "    pass",
        "dies on SIGPIPE",
    ),
    Mutation(
        "the parse guard is removed",
        "src/cli.py",
        "    try:\n"
        "        content_type, data = detect_content_type(raw_text)\n"
        "    except Exception as exc:\n"
        '        note(f"could not parse ({type(exc).__name__}: {exc})'
        ' — passed through unchanged")\n'
        "        sys.stdout.buffer.write(raw_bytes)\n"
        "        return 0",
        "    content_type, data = detect_content_type(raw_text)",
        "dies in the parser",
    ),
    Mutation(
        "the compression guard is removed",
        "src/cli.py",
        "    try:\n"
        "        text, notes = compress_json(data, original_text=raw_text, store=backing)\n"
        "    except Exception as exc:\n"
        '        note(f"could not compress ({type(exc).__name__}: {exc})'
        ' — passed through unchanged")\n'
        "        sys.stdout.buffer.write(raw_bytes)\n"
        "        return 0",
        "    text, notes = compress_json(data, original_text=raw_text, store=backing)",
        "dies in compression",
    ),
    Mutation(
        "compress.py loses its entry point entirely",
        "src/compress.py",
        'if __name__ == "__main__":',
        'if False:',
        "compress.py entry point",
    ),
    # Format-level, so guarded by property_test.py rather than cli_test.py.
    Mutation(
        "dictionary encoding is switched off",
        "src/table.py",
        "    dictionaries = dictionary_columns(varying_rows)",
        "    dictionaries = {}",
        "MUST_DICTIONARY",
        gate="property_test.py",
    ),
    Mutation(
        "the #dict line is written but the cells are never indexed",
        "src/table.py",
        "    varying_rows = remove_dictionaries(varying_rows, dictionaries)",
        "    varying_rows = varying_rows",
        "MUST_DICTIONARY",
        gate="property_test.py",
    ),
    Mutation(
        "a dictionary merges values Python calls equal (0 with 0.0)",
        "src/table.py",
        "        if not any(_same_value(value, seen) for seen in distinct):",
        "        if not any(value == seen for seen in distinct):",
        "MUST_DICTIONARY",
        gate="property_test.py",
    ),
    # The store. Same reasoning as the dictionary mutations — format-level
    # behaviour, so property_test.py is the gate, and each names the specific
    # check that must go red rather than "the suite fails".
    Mutation(
        "the store is switched off: nothing is ever stashed",
        "src/store.py",
        "            if gain < min_saving:\n"
        "                continue",
        "            if True:\n"
        "                continue",
        "MUST_STORE (no #store line)",
        gate="property_test.py",
    ),
    Mutation(
        "cells are stashed but the #store line is never written",
        "src/compress.py",
        '                table["store"] = doc_id',
        "                pass",
        "MUST_STORE",
        gate="property_test.py",
    ),
    Mutation(
        "the store commits even when the document is abandoned",
        "src/compress.py",
        "    if pending and final is text:",
        "    if pending:",
        "abandoned document left content behind",
        gate="property_test.py",
    ),
    Mutation(
        "a document decompresses without the store its values live in",
        "src/decompress.py",
        "        if store is None:",
        "        if False:",
        "MUST_STORE (decompressed without its store)",
        gate="property_test.py",
    ),
    Mutation(
        "the index keeps entries the document never references",
        "src/store.py",
        "def orphaned_ids(index, used_ids):",
        "def orphaned_ids(index, used_ids):\n    return []",
        "completeness detectors do not detect",
        gate="property_test.py",
    ),
    Mutation(
        "a missing object in the store is reported as fine",
        "src/store.py",
        "    return sorted(cell_id for cell_id, name in index.items() if not store.has(name))",
        "    return []",
        "completeness detectors do not detect",
        gate="property_test.py",
    ),
    Mutation(
        "stored objects are read back in text mode (CRLF becomes LF)",
        "src/store.py",
        '            with open(path, "rb") as handle:\n'
        "                if handle.read() != data:",
        '            with open(path, encoding="utf-8") as handle:\n'
        "                if handle.read() != text:",
        "FileStore does not survive real bytes",
        gate="property_test.py",
    ),
    # The MCP server. A third process boundary, so a third gate.
    Mutation(
        "the batch fetch quietly returns only the first id",
        "src/mcp_server.py",
        "    for cell_id in ids:",
        "    for cell_id in ids[:1]:",
        "a batch is not just the first id",
        gate="mcp_test.py",
    ),
    Mutation(
        "the response cap is removed",
        "src/mcp_server.py",
        "        if spent + cost > MAX_RESPONSE_TOKENS:",
        "        if False:",
        "over-cap batch truncates",
        gate="mcp_test.py",
    ),
    Mutation(
        "a missing object is returned as an empty value",
        "src/mcp_server.py",
        '            parts.append(f"[{key}] MISSING from the store'
        ' — the content is gone, not empty")',
        '            parts.append(f"[{key}]\\n")',
        "reported as MISSING",
        gate="mcp_test.py",
    ),
    Mutation(
        "query returns the whole value instead of the matching spans",
        "src/mcp_server.py",
        '            content = "\\n…\\n".join(spans)',
        "            content = content",
        "returns the matching span",
        gate="mcp_test.py",
    ),
    Mutation(
        "bin/margin loses its symlink-resolution loop",
        "bin/margin",
        'while [ -L "$src" ]; do\n'
        '  dir=$(cd -P "$(dirname "$src")" && pwd)\n'
        "  src=$(readlink \"$src\")\n"
        "  # A relative link is relative to the directory holding the link, not to $PWD.\n"
        '  [[ $src != /* ]] && src="$dir/$src"\n'
        "done",
        ":",
        "resolved the symlink",
    ),
]


def surviving(mutation):
    """Apply one mutation to a throwaway copy and run the gate against it.

    Returns None if the gate caught it, or a sentence saying how it did not.

    A copy, never the working tree — this file breaks things on purpose and must
    not be able to leave them broken. __pycache__ is left behind so a stale .pyc
    cannot answer for a source file we just rewrote.
    """
    with tempfile.TemporaryDirectory() as tree:
        for directory in ("src", "bin"):
            shutil.copytree(os.path.join(REPO, directory), os.path.join(tree, directory),
                            ignore=shutil.ignore_patterns("__pycache__"))

        target = os.path.join(tree, mutation.path)
        source = open(target, encoding="utf-8").read()
        if mutation.old not in source:
            # Not a skip. A mutation whose anchor has moved is testing nothing,
            # and silently testing nothing is the exact failure this file
            # exists to prevent (Rule 12). Fail loudly and fix the anchor.
            return f"anchor text not found in {mutation.path} — the mutation never applied"
        with open(target, "w", encoding="utf-8") as f:
            f.write(source.replace(mutation.old, mutation.new, 1))

        done = subprocess.run([sys.executable, os.path.join(tree, "src", mutation.gate)],
                              capture_output=True, text=True, check=False)

        if done.returncode == 0:
            return f"{mutation.gate} passed — nothing noticed"

        # Not just "something failed": the check NAMED for this behaviour has to
        # be the one that failed. Otherwise a mutation that happens to break an
        # unrelated check would look like coverage it does not have.
        failed = [line for line in done.stdout.splitlines() if line.strip().startswith("FAIL")]
        if not any(mutation.must_fail in line for line in failed):
            return (f"the gate failed, but not at {mutation.must_fail!r} — "
                    f"failures were: {[line.strip()[6:] for line in failed]}")
    return None


def baseline_passes(gates):
    """Does every gate pass on an unmutated copy?

    Without this the whole file is decoration, and it took about ninety seconds
    to prove it: shrinking the CLI test's pipe fixture back under the buffer
    made two checks fail before any mutation was applied, so every mutation
    "failed the gate" for free and all eight reported as caught. A red baseline
    turns a mutation suite into a machine that always says yes.

    That is instance seven of the habit this file was written to end, found in
    the file itself. Which is the argument for the file: the check is cheap, and
    the mistake is apparently not one I stop making by intending to.

    Every gate any mutation targets, not just one: once mutations can name their
    own gate, a red property_test.py would hand every dictionary mutation a free
    "caught" while cli_test.py sat green — the same hole, one gate along.
    """
    with tempfile.TemporaryDirectory() as tree:
        for directory in ("src", "bin"):
            shutil.copytree(os.path.join(REPO, directory), os.path.join(tree, directory),
                            ignore=shutil.ignore_patterns("__pycache__"))
        for gate in sorted(gates):
            done = subprocess.run([sys.executable, os.path.join(tree, "src", gate)],
                                  capture_output=True, text=True, check=False)
            if done.returncode != 0:
                return False, f"{gate}:\n{done.stdout}"
    return True, ""


def main():
    print(f"Mutation coverage — {len(MUTATIONS)} deliberate breaks\n")

    clean, output = baseline_passes({m.gate for m in MUTATIONS})
    if not clean:
        print("  a gate is RED before any mutation — these results would be meaningless.")
        for line in output.splitlines():
            if line.strip().startswith("FAIL"):
                print(f"    {line.strip()}")
        print("\nFix that gate first, then re-run.")
        return 1

    # In parallel: each one runs the whole CLI gate (about two seconds), and
    # eight of those in series is slow enough that a gate gets skipped, which is
    # how a gate stops being a gate.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(MUTATIONS)) as pool:
        results = list(pool.map(surviving, MUTATIONS))

    survivors = []
    for mutation, problem in zip(MUTATIONS, results):
        print(f"  {'SURVIVED' if problem else 'caught  '}  {mutation.name}")
        if problem:
            survivors.append((mutation, problem))

    print()
    if survivors:
        print(f"{len(survivors)} mutation(s) SURVIVED — the gate has a hole:")
        for mutation, problem in survivors:
            print(f"  - {mutation.name}: {problem}")
            print(f"    expected to fail: {mutation.must_fail!r} in {mutation.path}")
        return 1
    print(f"all {len(MUTATIONS)} mutations caught by the check written for them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
