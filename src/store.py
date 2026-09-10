"""The content store: bulk cells live here instead of in the prompt.

A cell whose text costs more than MIN_STORE_SAVING tokens is replaced in the
document by a handle, and its content moves here. The model fetches only what a
question actually needs. Measured worth on the sample set, before any of this
was written: 41.6% -> 74.6% overall, and 93% on github_issues — see
src/measure_store.py, which renders the documents rather than modelling them.

## Why two levels, and not one

The document carries a short per-document id (`0001`), and this store keeps a
per-document index mapping that id to a content hash. Files on disk are named by
hash.

The obvious design is to put the hash straight in the document and skip the
index. It was measured and rejected: a 12-hex handle costs 1.8 percentage points
across the sample set (~2,200 tokens) against a four-digit id, and the whole
point of this stage is tokens in the prompt. The index buys the short id back
without giving up content addressing, because the index is read from disk and
never enters a prompt.

What content addressing is for, and why it survives the indirection:

- **Dedup on disk.** The same issue body fetched twice is stored once.
- **Integrity.** A stored object's name is a claim about its bytes, and
  `get` checks that claim rather than trusting the filename.

## Reversibility

CLAUDE.md's first decision is that originals are always kept, never
delete-and-hope. Here the store *is* the original: a document plus its store
round-trips exactly, and a document without its store is missing data. That is
why `get` raises rather than returning a placeholder, and why
`missing_handles()` exists — silent partial output is the one outcome this
project rules out, and a store is the first thing here that can lose half a
document while looking fine.
"""

import csv
import hashlib
import io
import json
import os
import tempfile

import render
from tokens import token_count

# Tokens a single cell must save before its content is worth moving out of the
# document. Measured 2026-09-10 by src/measure_store.py, which renders the whole
# sample set at each candidate bar rather than pricing cells in isolation:
#
#     bar    cells stored   set total
#       1            257        73.3%
#      10            187        73.1%
#      20            166        72.8%
#      50             67        70.1%
#
# The curve is flat to 20 and falls away after it, so 20 keeps 99% of the saving
# while storing 91 fewer cells. It is positive rather than zero for the same
# reason MIN_DICT_SAVING is: a cell that breaks even still costs the reader a
# fetch to recover a value it could have read in place, and a fetch is far more
# expensive to a reader than a dictionary lookup.
MIN_STORE_SAVING = 20

# 24 hex characters, 96 bits, for both the content hash and the document id.
# A collision serves one cell's content for another's, which is data loss, so
# this is sized against the failure and not against disk.
#
# It is very nearly free. The content hash is not in the document at all — that
# is what the id/index indirection bought — so its width costs nothing. The
# document id *is* in the document, on the #store line, and widening it from 12
# to 24 measured at **1 token per document**; 21 tokens across the whole sample
# set, which leaves the set total at 74.4%. Nothing to economise against.
#
# A doc_id collision is worse than an object collision, which is the other
# reason both are wide: two payloads sharing a doc_id means one document's
# handles resolve against the other's index, and every handle then returns
# *plausible wrong content* rather than failing.
#
#   objects   48 bits   96 bits
#      10^4   1.8e-07   6.3e-22
#      10^6   1.8e-03   6.3e-18
#      10^7   1.8e-01   6.3e-16
#
# **This was 12 (48 bits), and the comment justifying it was wrong in a way
# worth keeping** (harness.md Rule 5): it said "under 10^-9 at the ~10^4
# distinct cells across the whole sample set". The arithmetic was right and the
# *scope* was wrong. A sample set is not the population — this store is one
# global namespace with no eviction that accumulates for years, so n is total
# objects ever written, not cells in one run. At 10^6 the real figure is 0.18%,
# and a personal store reaches 10^6 in a year of piping curl output.
#
# Found by comparing against Headroom, which uses 96 bits here and documents it
# against birthday bounds. Their store survives at any width because it evicts
# on a 30-minute TTL, which is what keeps their n small; ours has no such bound,
# and that difference is exactly what the original comment failed to notice.
#
# `put` also refuses to overwrite an existing object whose bytes differ. That is
# a backstop, not a mitigation: it converts a collision into a hard failure in
# the *compress* path — a payload that inexplicably will not compress — so it
# argues for a wider hash rather than excusing a narrow one.
HASH_WIDTH = 24

# Four digits, zero padded, so ids sort and read consistently. Measured at
# ~2 tokens against ~6 for a 12-hex hash. 9,999 stored cells in one document is
# far past anything the sample set reaches; `_next_id` raises rather than
# wrapping if it is ever hit.
ID_WIDTH = 4
MAX_IDS = 10 ** ID_WIDTH


def content_hash(text):
    """The name a piece of content is filed under."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_WIDTH]


def document_id(payload_text):
    """The name a document's index is filed under.

    Derived from the payload rather than from the rendered document, which would
    be circular: the document cannot contain the id of an index built while
    rendering it. A content-derived id also means recompressing the same payload
    reuses its index instead of accumulating one per run.
    """
    return hashlib.sha256(payload_text.encode("utf-8")).hexdigest()[:HASH_WIDTH]


def format_id(number):
    """1 -> "0001"."""
    if number >= MAX_IDS:
        raise ValueError(
            f"more than {MAX_IDS - 1} stored cells in one document; "
            f"ID_WIDTH would have to grow, and every existing index with it"
        )
    return str(number).zfill(ID_WIDTH)


class MemoryStore:
    """A store that never touches the disk.

    Not a test double bolted on afterwards: property_test.py runs thousands of
    trials, and a gate that writes thousands of files is a gate that gets
    bypassed for being slow — which docs/harness.md Rule 5 notes is the same as
    not having it. FileStore below is the same interface against a directory.
    """

    def __init__(self):
        self.objects = {}
        self.indexes = {}

    def put(self, text):
        name = content_hash(text)
        existing = self.objects.get(name)
        if existing is not None and existing != text:
            raise ValueError(f"hash collision on {name} — refusing to overwrite")
        self.objects[name] = text
        return name

    def get(self, name):
        if name not in self.objects:
            raise KeyError(name)
        text = self.objects[name]
        if content_hash(text) != name:
            raise ValueError(f"{name} does not hash to its own name — content changed")
        return text

    def has(self, name):
        return name in self.objects

    def write_index(self, doc_id, mapping):
        self.indexes[doc_id] = dict(mapping)

    def read_index(self, doc_id):
        if doc_id not in self.indexes:
            raise KeyError(doc_id)
        return dict(self.indexes[doc_id])


class FileStore:
    """The same store, under a directory.

    Layout:
        <root>/objects/<hash>      one stored cell, verbatim UTF-8
        <root>/docs/<doc_id>.json  {"0001": "<hash>", ...}

    Objects are shared across every document; indexes are per document. Deleting
    an index orphans objects rather than losing them, and deleting an object
    breaks every document that referenced it — which `missing_handles` reports
    rather than letting a decompression quietly return half a payload.
    """

    def __init__(self, root):
        self.root = os.path.expanduser(root)

    def _object_path(self, name):
        return os.path.join(self.root, "objects", name)

    def _index_path(self, doc_id):
        return os.path.join(self.root, "docs", doc_id + ".json")

    # Objects are read and written as BYTES, never in text mode.
    #
    # This was text mode, and it was broken for any content containing a
    # carriage return — which is most real prose, because GitHub issue bodies
    # are CRLF. Python's text mode applies universal-newline translation on
    # read, so `\r\n` came back as `\n`, the read-back never equalled what was
    # written, and the two places that compare content both misread that as
    # corruption: `put` reported a hash collision and refused to store, and
    # `get` would have reported that the content had changed under it. Both
    # messages were confidently wrong about the cause.
    #
    # No gate saw it, because every gate used MemoryStore — property_test.py
    # runs thousands of trials and a store that writes files is a store nobody
    # keeps in a gate. So FileStore, the implementation that actually ships, had
    # no coverage at all. That is Rule 12 at its most literal: the environment
    # the check runs in was not the environment the code runs in. Found by
    # running `margin --store` on a real payload, which is Rule 11's argument
    # for exercising the everyday invocation.
    def put(self, text):
        data = text.encode("utf-8")
        name = content_hash(text)
        path = self._object_path(name)
        if os.path.exists(path):
            # Read before writing rather than assuming the name proves the
            # bytes. If these ever differ it is a collision, and overwriting
            # would destroy whichever cell got there first.
            with open(path, "rb") as handle:
                if handle.read() != data:
                    raise ValueError(f"hash collision on {name} — refusing to overwrite")
            return name
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return name

    def get(self, name):
        try:
            with open(self._object_path(name), "rb") as handle:
                text = handle.read().decode("utf-8")
        except FileNotFoundError:
            raise KeyError(name) from None
        if content_hash(text) != name:
            raise ValueError(f"{name} does not hash to its own name — content changed")
        return text

    def has(self, name):
        return os.path.exists(self._object_path(name))

    def write_index(self, doc_id, mapping):
        """Atomically, because this is the only mutable file in the store.

        Objects are content-addressed: writing one twice writes the same bytes,
        so a torn write there is harmless. An index is not — two `margin`
        invocations racing on one doc_id could interleave into a half-written
        JSON file, and a corrupt index means every handle in that document
        resolves to nothing. Temp file in the *same directory* (os.replace is
        only atomic within a filesystem), then replace.
        """
        path = self._index_path(doc_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(mapping, handle, separators=(",", ":"), sort_keys=True)
            os.replace(temp, path)
        except BaseException:
            # Leaving a .tmp behind would be a slow leak of files nothing reads.
            if os.path.exists(temp):
                os.unlink(temp)
            raise

    def read_index(self, doc_id):
        try:
            with open(self._index_path(doc_id), encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            raise KeyError(doc_id) from None


def default_root():
    """Where a store lives when nobody says otherwise.

    Under the home directory, not the working directory: a document compressed in
    one directory and read in another must still resolve its handles, and a
    relative root would make "can I read this document" depend on where you
    happen to be standing.
    """
    return os.environ.get("MARGIN_STORE") or os.path.join("~", ".margin", "store")


def csv_field_cost(text):
    """Tokens for one cell as the document writes it, quoting included.

    render.py writes cells through csv.writer, which wraps a field in quotes and
    doubles interior quotes when it contains a comma, a quote or a newline. Prose
    cells hit all three, so pricing the bare text under-charges exactly the cells
    this decision is about. docs/thresholds.md records the #dict cost model
    getting the same thing wrong from the other direction.

    The field separator is common to a cell and to the handle replacing it, so it
    cancels out of the comparison and is not counted.
    """
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow([text])
    return token_count(buffer.getvalue())


def stash_bulk_cells(table, min_saving=MIN_STORE_SAVING):
    """Replace qualifying cells with handles. Returns {id: content text}.

    Mutates table["cells"]. Ids are assigned in the order cells are met, so a
    document reads 0001, 0002, ... down the page; identical content still gets a
    separate id per occurrence, because the document pays per handle either way
    and sharing ids would make the index depend on cell order for nothing.

    The content is `render.encode_cell(value)` — the document's own text for that
    value, not the raw Python object. That is what makes restoring exact:
    decode_cell(encode_cell(v)) is already the invariant the whole format rests
    on, so the store inherits it rather than needing a second one.

    **Nothing is written here.** This returns content for `commit` to write later,
    because compress_json can still abandon the table after this point — the
    round-trip may not verify, the saving may fall under MIN_TABLE_SAVING, or the
    output may simply be longer than the input. The first version wrote as it
    went, and every one of those paths left the store holding objects and an
    index belonging to a document that was never emitted. Found by
    property_test.py's random sweep within a minute of the check existing, which
    is the argument for aiming a check at the claim rather than at the data:
    every one of those runs round-tripped perfectly.
    """
    types = [column["type"] for column in table["columns"]]

    pending = {}
    for row in table["cells"]:
        for position, (value, type_name) in enumerate(zip(row, types)):
            if isinstance(value, render.Handle):
                continue
            text = render.encode_cell(value, type_name)

            # The size travels with the handle so the reader can decide whether
            # to spend it. token_count of the bare text, not the CSV-quoted
            # field: it is what the *fetch* will cost the reader, not what the
            # cell costs the document.
            cell_id = format_id(len(pending) + 1)
            handle = render.Handle(cell_id, token_count(text))

            # Priced against the handle this cell would ACTUALLY get, lure and
            # all, rather than against a bare `\@0001`. The two differ by the
            # width of the token count, so a bare-handle threshold would admit
            # cells that save less than min_saving once written. Same shape as
            # Rule 4 — one notion of what a handle costs, not two — and the same
            # trap docs/thresholds.md records for #dict, where pricing the
            # status quo with the wrong encoder made every dictionary look
            # better than it was.
            gain = csv_field_cost(text) - csv_field_cost(render.encode_cell(handle))
            if gain < min_saving:
                continue

            pending[cell_id] = text
            row[position] = handle
    return pending


def staged(doc_id, pending):
    """A throwaway store holding `pending`, for verifying before committing.

    compress_json's round-trip guard has to resolve the handles it just wrote,
    and it has to do that *before* deciding whether to keep the document. Giving
    it the real store would mean writing content the pipeline may then throw
    away — so it gets this instead, and the real store sees nothing until commit.
    """
    staging = MemoryStore()
    staging.write_index(doc_id, {cid: staging.put(text) for cid, text in pending.items()})
    return staging


def commit(store, doc_id, pending):
    """Write the staged content and its index, once the document is final."""
    index = {cell_id: store.put(text) for cell_id, text in pending.items()}
    store.write_index(doc_id, index)
    return index


def used_ids(table):
    """Every handle id the document actually references, in page order."""
    return [
        value.cell_id
        for row in table["cells"]
        for value in row
        if isinstance(value, render.Handle)
    ]


def restore_handles(table, index, store):
    """Replace every Handle with its content. Mutates table["cells"].

    Raises rather than substituting a placeholder when an id is not in the index
    or its content is not in the store. A store that quietly returns "" for a
    missing body would decompress to a payload that looks complete and is not,
    which is the exact failure "never delete-and-hope" exists to rule out.
    """
    types = [column["type"] for column in table["columns"]]
    for row in table["cells"]:
        for position, (value, type_name) in enumerate(zip(row, types)):
            if not isinstance(value, render.Handle):
                continue
            if value.cell_id not in index:
                raise KeyError(
                    f"handle {value.cell_id} is not in the store index — "
                    f"the document and its index disagree"
                )
            row[position] = render.decode_cell(store.get(index[value.cell_id]), type_name)


def missing_handles(index, store):
    """Ids in this document's index whose content is not in the store.

    The check Rule 3 asks for. A handle is a *claim* that content exists
    somewhere, and a round-trip can only see that claim while the store happens
    to be present — so the claim needs an assertion aimed at it. Returns the
    broken ids, so a caller can name them rather than reporting that something,
    somewhere, is absent.
    """
    return sorted(cell_id for cell_id, name in index.items() if not store.has(name))


def orphaned_ids(index, used_ids):
    """Ids the index carries that the document never references.

    The other half of completeness, and the half a round-trip is guaranteed not
    to notice: an index with extra entries decompresses perfectly. It means the
    writer and the document disagree about what was stored, which is Rule 4's
    shape — and it is how a store silently grows forever.
    """
    return sorted(set(index) - set(used_ids))
