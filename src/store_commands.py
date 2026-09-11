"""`margin fsck`, `gc`, `export`, `import` — keeping the store recoverable.

`docs/store.md` carried these as "Not built, and said out loud" from the day the
store shipped: no bundle unit ("a document is meaningless without its index and
objects — a real gap"), and no way to find an object no index points at ("a lost
index leaks objects forever").

## Why these four when `--decompress` was refused

`docs/cli.md` kept `--decompress` out on scope — one job — and pointed at
`python src/decompress.py` for the rare occasion. That argument does not reach
these. `--decompress` is a convenience for something another entry point already
does; **a store with no `fsck` and no bundle unit is unrecoverable by any other
means.** There is no second way to find a leaked object, and no second way to
move a document to another machine with the content it needs. That is the
difference, and it is the whole justification.

## Why a separate module when all input is read in cli.py

It still is. `cli.py` owns argv, decides that the first word is a subcommand, and
hands the rest here; nothing in this file touches `sys.argv`. What lives here is
what each command *does*, so that the everyday `margin f.json` path — the one the
whole CLI is shaped around — reads exactly as it did before any of this existed.

## What none of them do

Compress anything. These operate on a store that already exists, which is why
they are not in the pipeline and why `margin` with no subcommand is untouched.
"""

import json
import os
import sys

import render
import store as store_module

EXIT_USAGE = 2
EXIT_PROBLEM = 1

# Named here rather than inferred, so `margin export` cannot be mistaken for a
# request to compress a file named `export`. cli.py checks this set before it
# treats anything as a path, and docs/cli.md states the cost of that: to compress
# a file literally called `export`, write `margin ./export` or pipe it in.
COMMANDS = ("fsck", "gc", "export", "import")

USAGE = """usage: margin <command> [args]

  fsck                    check every index against the objects it names
  gc [--delete]           find objects no index references (dry run by default)
  export <doc-id> <file>  write a document's index and objects as one bundle
  import <file>           read a bundle into this store

The store is $MARGIN_STORE, or ~/.margin/store."""


def _open_store():
    """The same store the MCP server and the CLI use, resolved the same way."""
    return store_module.FileStore(store_module.default_root())


def _say(message):
    print(f"margin: {message}", file=sys.stderr)


def fsck(argv):
    """Report what is broken and what is leaked. Never changes anything.

    Two different faults, and they are not symmetrical:

      broken   an index names content that is absent or no longer hashes to its
               own name. A document that needs it is already unreadable, and no
               amount of tidying brings it back — this is data loss, reported so
               it is known rather than discovered mid-answer.
      leaked   an object no index references. Costs disk and nothing else; `gc`
               is what removes it. docs/store.md named this one specifically:
               "a lost index leaks objects forever".
    """
    if argv:
        _say(f"fsck takes no arguments — got {argv[0]}\n{USAGE}")
        return EXIT_USAGE

    store = _open_store()
    documents = store.document_ids()
    objects = store.object_names()
    broken = store_module.broken_objects(store)
    leaked = store_module.leaked_objects(store)

    print(f"{len(documents)} document(s), {len(objects)} object(s)")

    if broken:
        print(f"\n{len(broken)} BROKEN — content a document needs is gone:")
        for doc_id, cell_id, name, why in broken[:20]:
            print(f"  {doc_id} {cell_id} -> {name}  ({why})")
        if len(broken) > 20:
            print(f"  ... and {len(broken) - 20} more")

    if leaked:
        print(f"\n{len(leaked)} LEAKED — no index references these:")
        for name in leaked[:20]:
            print(f"  {name}")
        if len(leaked) > 20:
            print(f"  ... and {len(leaked) - 20} more")
        print("\n`margin gc` lists them; `margin gc --delete` removes them.")

    if not broken and not leaked:
        print("\nclean")
    # Leaked objects are not a failure — they are disk. Broken content is.
    return EXIT_PROBLEM if broken else 0


def gc(argv):
    """Remove objects no index references. Dry run unless told otherwise.

    **The only irreversible thing in this project**, so it does nothing by
    default and says what it would do instead. CLAUDE.md's first decision is
    that originals are kept and never delete-and-hope; a sweep that ran on
    sight would be exactly that.

    Marks from EVERY index, not from a document handed in. Dedup is a
    cross-document property (docs/store.md): an object this document stopped
    using may be the only copy another document has. That is why the mark set
    comes from `store.reachable_objects` and not from one `used_ids` call.
    """
    delete = False
    for arg in argv:
        if arg == "--delete":
            delete = True
        else:
            _say(f"unknown option {arg}\n{USAGE}")
            return EXIT_USAGE

    store = _open_store()
    leaked = store_module.leaked_objects(store)
    kept = len(store.object_names()) - len(leaked)

    if not leaked:
        print(f"nothing to collect — all {kept} object(s) are referenced")
        return 0

    print(f"{len(leaked)} unreferenced object(s), {kept} kept:")
    for name in leaked[:20]:
        print(f"  {name}")
    if len(leaked) > 20:
        print(f"  ... and {len(leaked) - 20} more")

    if not delete:
        print("\nnothing was deleted. Re-run with --delete to remove them.")
        return 0

    removed = store_module.sweep(store, leaked)
    print(f"\ndeleted {removed} object(s)")
    return 0


def export(argv):
    """Write one document's index and every object it needs to a bundle.

    Takes the document id, which is on the `#store` line of any stored document,
    and accepts it with or without the quotes that line shows — a caller copying
    `#store"e8f9..."` out of a document should not have to know which half to
    keep.
    """
    if len(argv) != 2:
        _say(f"export needs a document id and a file\n{USAGE}")
        return EXIT_USAGE
    doc_id, path = argv
    doc_id = doc_id.strip().strip('"').strip()

    store = _open_store()
    try:
        payload = store_module.bundle(store, doc_id)
    except KeyError as exc:
        # Either the document is not here, or it is here and incomplete. Both
        # are refusals rather than a half-written bundle: a bundle that resolves
        # only on the machine that made it is worse than not having the command.
        _say(str(exc).strip('"'))
        return EXIT_PROBLEM

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"{doc_id}: {len(payload['index'])} handle(s), "
          f"{len(payload['objects'])} object(s) -> {path}")
    return 0


def import_(argv):
    """Read a bundle into this store.

    Goes through `store.unbundle`, which goes through `store.commit`, so it
    inherits the refusal to overwrite an object whose bytes differ rather than
    reimplementing it (Rule 14). Importing the same bundle twice is a no-op by
    content addressing, not by a check here.
    """
    if len(argv) != 1:
        _say(f"import needs exactly one bundle file\n{USAGE}")
        return EXIT_USAGE
    path = argv[0]

    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        _say(f"cannot read {path}: {exc.strerror}")
        return EXIT_USAGE
    except json.JSONDecodeError as exc:
        _say(f"{path} is not JSON: {exc}")
        return EXIT_PROBLEM

    store = _open_store()
    try:
        doc_id, objects = store_module.unbundle(store, payload)
    except (KeyError, ValueError) as exc:
        _say(str(exc).strip('"'))
        return EXIT_PROBLEM

    print(f"imported {doc_id}: {objects} object(s) into "
          f"{store_module.default_root()}")
    return 0


def run(command, argv):
    """Dispatch. cli.py has already decided this is a subcommand."""
    if command == "fsck":
        return fsck(argv)
    if command == "gc":
        return gc(argv)
    if command == "export":
        return export(argv)
    if command == "import":
        return import_(argv)
    raise AssertionError(f"not a store command: {command}")
