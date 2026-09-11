"""Round-trip the compressor over randomly generated payloads.

The eval harness answers "do the answers survive on the two payloads I have?".
This answers a different question: "does the format survive shapes I have not
thought of?" Both real payloads together exercise maybe a dozen value shapes,
and every format bug found so far has been in the gaps between them:

  - a key containing a comma or a newline collided with the header syntax
  - a one-element array ["\\N"] joined to exactly the cell text meaning null
  - [] and "key absent" both rendered as an empty cell

Every one of those was found by hand-probing edge cases, which only finds the
cases someone thought to probe. This generates them instead.

The generator deliberately over-produces the things that have bitten before:
sentinel-lookalike strings, separator-bearing keys, empty containers, mixed-type
arrays, booleans next to ints. It is not trying to look like real API data — it
is trying to break the encoder.

A failure prints a shrunk payload small enough to paste straight into
FIXED_CASES below, which is where every past bug now lives permanently so it
cannot come back.

Usage: python src/property_test.py [trials]
"""

import json
import random
import sys
import tempfile

import render
import store
from compress import compress_json, strip_boilerplate
from render import FORMAT_MARKER, LEGEND_PREFIX
from decompress import decompress
from table import same_json

TRIALS = 2000
# Enough that a fresh run takes a couple of seconds and still explores widely.
# Measured 2026-09-08: 2,000 trials reproduce the known ["\N"] bug (deliberately
# reintroduced to check) within the first ~150 trials, so this has margin.

SEED = 20260908
# Fixed so a failure is reproducible and so the hooks do not flake: a gate that
# fails one commit in twenty gets bypassed, and a bypassed gate is not a gate.
# To hunt for new bugs, pass a different seed on the command line.

# Values chosen because each one broke something, or is one step away from a
# case that did.
AWKWARD_SCALARS = [
    None, True, False, 0, 1, -1, 3.0, 1.5, 10**20,
    "", " ", "s", "a,b", 'c"d', "e\nf", "g\r\nh",
    "\\N", "\\E", "\\A", "\\\\N", "\\",
    "日本語 café 🎉",
    "true", "null", "3.0",  # strings that look like other types
]

AWKWARD_ARRAYS = [
    [], [1], [1, 2, 3], [3, 2, 1], [1, 1, 2], [-5, 0, 5], [10**18, 10**18 + 1],
    ["a", "b"], ["\\N"], ["a b"], ["a,b"], [""], ["a", 1], [[1]], [{"z": 1}],
    [True, False],
]

SAFE_KEYS = ["a", "b", "c", "k:v", "url", "node_id", "_url", "user", "id"]

UNREPRESENTABLE_KEYS = ["x.y", "p,q", "n\nl"]
# The header is one line of comma-separated `name:type` specs, and flattening
# claims every dot it sees, so these three characters in a key cannot be
# expressed. That is a known, deliberate limitation: the compressor abandons the
# table and emits JSON. Kept out of the must-tabulate sweep — a payload using
# them is *supposed* to degrade, so asserting it tabulates would fail forever on
# working code.


def value_family(rng):
    """Pick how one column will behave, once, for the whole payload.

    Real API responses are homogeneous down a column: `points` is an int in
    every row, `_tags` an array of strings in every row. A generator that rolls
    an independent random type per cell produces columns that are uniformly
    `json`, and a table of json columns always loses MIN_TABLE_SAVING and comes
    back as plain JSON — which round-trips trivially. Measured 2026-09-08: with
    per-cell types, 0 of 2,000 trials built a table at all. The whole random
    sweep was testing json.dumps.

    So the family is chosen per column and the *values* vary within it, with an
    occasional awkward value injected to keep the edges reachable.
    """
    kind = rng.choice(["int", "str", "sorted_ints", "strs", "nested", "mixed", "few"])

    # "few" is the dictionary path, and it needs its own family for the reason
    # this whole function exists. Every other family draws from a wide range, so
    # a column almost never repeats a value often enough to earn a #dict — the
    # sweep would have run 2,000 trials past the new encoder without once
    # reaching it, exactly as per-cell types once ran 2,000 trials without
    # building a single table. The values are long enough to clear
    # MIN_DICT_SAVING, because a dictionary of short values correctly loses.
    few = [
        "CONTRIBUTOR ACCESS LEVEL",
        "COLLABORATOR ACCESS LEVEL",
        {"label": "needs triage", "colour": "ededed", "default": False},
    ]

    def generate():
        if rng.random() < 0.12:  # keep the known-nasty values reachable
            return rng.choice(AWKWARD_SCALARS + AWKWARD_ARRAYS)
        if kind == "int":
            return rng.randint(-10**9, 10**9)
        if kind == "str":
            return rng.choice(["alpha", "beta", "a,b", 'c"d', "e\nf", "日本語 🎉", ""])
        if kind == "sorted_ints":
            start = rng.randint(0, 10**8)
            return sorted(start + rng.randint(0, 400) for _ in range(rng.randint(0, 25)))
        if kind == "strs":
            return [rng.choice(["p", "q", "r_s", "story"]) for _ in range(rng.randint(0, 4))]
        if kind == "nested":
            return {"m": rng.choice([0, 1, None]), "v": {"w": rng.choice(["x", "y"])}}
        if kind == "few":
            return rng.choice(few)
        return rng.choice(AWKWARD_SCALARS)

    return generate


def random_payload(rng, keys):
    """A list of similar objects — the shape the compressor is built to tabulate.

    6-20 rows, not 2-3: a tiny payload almost always fails MIN_TABLE_SAVING and
    comes back as JSON, so small payloads spend their trials on the fallback
    path rather than the encoder.
    """
    chosen = rng.sample(keys, rng.randint(1, min(5, len(keys))))
    families = {key: value_family(rng) for key in chosen}
    rows = [
        {
            "i": index,
            **{k: gen() for k, gen in families.items() if rng.random() < 0.85},
        }
        for index in range(rng.randint(6, 20))
    ]
    return _wrap(rows, rng)


def _wrap(rows, rng):
    """Bury the records in an envelope, the way most real APIs ship them.

    A bare top-level array is the minority case — GitHub does it, but Algolia
    wraps in `hits`, JSON:API and GraphQL in `data`, and plenty of others go
    deeper. Generating only bare arrays is why a payload shaped
    {"data": {"items": [...]}} reached 2026-09-08 compressing by 0%: nothing in
    the sweep had that shape, so nothing failed.

    Metadata siblings are added at each level so the skeleton actually has
    something to lose if compress.skeleton or decompress._nest drops it.
    """
    depth = rng.choice([0, 0, 1, 1, 2, 3])  # bare arrays still common, not dominant
    node = rows
    for level in range(depth):
        key = rng.choice(["data", "items", "hits", "results", "r"])
        node = {key: node, f"meta{level}": rng.choice([1, "m", None, {"n": 2}])}
    return node


# Notes that mean the table itself is broken, as opposed to notes that mean the
# payload was never worth tabulating.
BROKEN_TABLE_NOTES = ("ROUND-TRIP MISMATCH", "table render/parse failed")


def round_trips(payload):
    """The invariant: decompress(compress(x)) == strip_boilerplate(x), AND the
    table was not silently abandoned to get there.

    strip_boilerplate is on the right because it is the lossy stage by choice —
    the URL fields it drops are the product, not an accident. Everything after
    it must be exact. A crash counts as a failure: compress_json's contract is
    to fall back to plain JSON when it cannot prove a table correct, never to
    raise.

    The notes check is not belt-and-braces, it is the whole test. compress_json
    verifies its own round-trip and emits plain JSON when it fails, so a
    completely broken table encoder still satisfies the equality above — the
    fallback round-trips trivially. Checked by deliberately reintroducing the
    ["\\N"] bug: 2,000 trials reported zero failures until this was added.

    The eval harness learned the same lesson in its own NOTES section. Two
    different tests, one shared trap: a safe fallback makes a broken format look
    correct, so any test of a format has to assert the format was actually used.

    Notes about a payload not being worth a table (too few rows, no record
    array, saving below the gate) are fine — that is the gate working. Only a
    mismatch or a render/parse failure means the encoder is wrong.
    """
    try:
        text, notes = compress_json(payload)
        if any(broken in note for note in notes for broken in BROKEN_TABLE_NOTES):
            return False
        return same_json(decompress(text), strip_boilerplate(payload))
    except Exception:
        return False


def store_round_trips(payload):
    """The invariant again, with a store behind it: content comes back exact.

    Same shape as round_trips, and the same trap one layer along — a store that
    wrote nothing leaves every cell in the document, which round-trips perfectly
    while doing none of the job. MUST_STORE asserts the #store line separately;
    this asserts the data survives the trip through it.
    """
    try:
        st = store.MemoryStore()
        text, notes = compress_json(payload, store=st)
        if any(broken in note for note in notes for broken in BROKEN_TABLE_NOTES):
            return False
        return same_json(decompress(text, st), strip_boilerplate(payload))
    except Exception:
        return False


def store_index_is_complete(payload):
    """Every handle resolves, and the index carries nothing the document lost.

    Rule 3's shape, at the store. A handle is a *claim* that content exists
    somewhere, and `decompress(compress(x)) == x` can only ever see that claim
    while the store happens to be sitting right there — it cannot see a dangling
    id, and it definitely cannot see an index entry the document never
    references, because an index with extra rows decompresses perfectly.

    Both halves matter and they fail differently: a missing object loses data,
    an orphaned entry means the writer and the document disagree about what was
    stored, and is how a store grows forever.
    """
    st = store.MemoryStore()
    text, _ = compress_json(payload, store=st)

    # The fallback path, and the one this check got wrong first: a payload the
    # table does not help comes back as plain JSON, which render.parse rightly
    # refuses. The interesting claim there is not "the index is consistent" but
    # "nothing was written at all" — because compress_json stashes cells before
    # it knows whether it will keep the table, and an abandoned document must
    # leave the store exactly as it found it.
    if not text.startswith(FORMAT_MARKER):
        return not st.objects and not st.indexes

    table = render.parse(text)
    doc_id = table.get("store")
    if not doc_id:
        # Nothing was stored. Then no index may have been written either — an
        # index for a document with no #store line is unreachable by
        # construction, which is the orphan case at document scale.
        return not st.indexes

    index = st.read_index(doc_id)
    if store.missing_handles(index, st):
        return False
    return not store.orphaned_ids(index, store.used_ids(table))


def stored_document_refuses_to_decompress_alone(payload):
    """A document whose values are in a store must not decompress without it.

    The failure this stage introduces that nothing else in the harness can see:
    silent partial output. Returning rows with unresolved handles sitting in
    them would serialise to something that looks like a payload, passes a
    JSON parse, and is missing exactly the content someone compressed it to
    keep. "Never delete-and-hope" is the first decision in CLAUDE.md, and this
    is the first thing built here that could break it.

    Vacuous unless the payload actually stored something, so that is checked
    first rather than assumed — Rule 12: a check that can only take the trivial
    branch is not a check.
    """
    try:
        st = store.MemoryStore()
        text, _ = compress_json(payload, store=st)
        if not render.parse(text).get("store"):
            return None  # nothing stored; this case has nothing to say
        try:
            decompress(text)
        except ValueError:
            return True
        return False  # it returned something, which is the bug
    except Exception:
        return False


def dedupes_across_documents():
    """One object on disk, two documents, and both still resolve.

    Content addressing earns its keep here and only here. Inside one document,
    repeated bulk content is taken by #const or #dict long before the store sees
    it — so this is the case that would silently never run if it were written as
    a single-payload property, which is Rule 12's shape: a check whose only
    reachable branch is the trivial one.

    Every value inside each document is distinct, so neither #const nor #dict
    claims the column and the cells reach the store — the two documents then
    share exactly one body between them. Getting this wrong is instructive and
    was done twice while writing it: a body repeated *within* a document is taken
    by #dict, and the test measured the store deduping something it never saw.
    """
    shared = f"{_BULK} shared between two documents"
    first = [{"i": index, "a": shared if index == 0 else f"{_BULK} first {index}"}
             for index in range(8)]
    second = [{"i": index, "a": shared if index == 0 else f"{_BULK} second {index}"}
              for index in range(8)]

    st = store.MemoryStore()
    first_text, _ = compress_json(first, store=st)
    objects_after_first = len(st.objects)
    second_text, _ = compress_json(second, store=st)

    # The premise, checked rather than assumed: if an existing rule claimed these
    # columns there would be nothing in the store and every count below would be
    # trivially satisfied by zero.
    if objects_after_first != 8:
        return False
    # The shared body must not have been written a second time.
    if len(st.objects) - objects_after_first != 7:
        return False
    # ...and both documents must still read back exactly.
    return (same_json(decompress(first_text, st), strip_boilerplate(first))
            and same_json(decompress(second_text, st), strip_boilerplate(second))
            and len(st.indexes) == 2)


def file_store_survives_real_bytes():
    """FileStore, against a real directory, with content that broke it.

    Every other store check runs on MemoryStore, because this file runs thousands
    of trials and a gate that writes thousands of files is a gate someone turns
    off. The consequence was that **the implementation that actually ships had no
    coverage at all** — and it was broken: objects were read back in text mode,
    so Python's universal-newline translation turned `\\r\\n` into `\\n`, the
    read-back never equalled what was written, and `put` reported a hash
    collision while `get` reported that the content had changed underneath it.
    Both messages were confidently wrong about the cause.

    Real prose is full of carriage returns — GitHub issue bodies are CRLF — so
    this was not an edge case, it was the main path. Rule 12: the environment a
    check runs in has to be the environment the code runs in, and for exactly one
    check here that has to be a real filesystem.

    So: a handful of payloads, once, on disk. Cheap enough to always run.
    """
    crlf = "A body with Windows line endings.\r\n\r\nSecond paragraph.\r\n" * 12
    payloads = [
        [{"i": index, "a": f"{crlf} row {index}"} for index in range(8)],
        # A lone \r too: text mode translates that as well, and it is the case a
        # \r\n-only fixture would silently stop covering.
        [{"i": index, "a": f"line one\rline two {_BULK} {index}"} for index in range(8)],
        bulky(),
    ]

    with tempfile.TemporaryDirectory() as root:
        st = store.FileStore(root)
        for payload in payloads:
            text, _ = compress_json(payload, store=st)
            if not text.startswith(FORMAT_MARKER):
                return False
            if render.STORE_PREFIX not in text:
                return False  # the premise: nothing stored means nothing tested
            if not same_json(decompress(text, st), strip_boilerplate(payload)):
                return False

        # Writing the same payload a second time must be a no-op, not a
        # collision — this is the exact shape the text-mode bug took, and
        # `put` is the only place that compares stored bytes against new ones.
        #
        # The RESULT is asserted, not just the call. The first version ran these
        # and looked at nothing, so once compress_json learned to degrade
        # gracefully on a failed store write, a reintroduced text-mode read fell
        # back to plain JSON and this check sailed past it. Rule 12: the
        # question is what the check lets through, and a call whose return value
        # is discarded lets through everything that fails quietly.
        for payload in payloads:
            again, notes = compress_json(payload, store=st)
            if render.STORE_PREFIX not in again:
                return False
            if any("store write failed" in note for note in notes):
                return False

        # And a store re-opened from the same directory still resolves — the
        # index has to survive being written and read as files, not just held.
        reopened = store.FileStore(root)
        text, _ = compress_json(payloads[0], store=reopened)
        return same_json(decompress(text, reopened), strip_boilerplate(payloads[0]))


def detects_a_broken_store():
    """Feed the completeness detectors a store that is actually broken.

    store_index_is_complete() runs on every fixed case and every random trial and
    has never once seen a real orphan or a real missing object, because normal
    operation does not produce them. That makes it a check whose only reachable
    branch is "nothing wrong" — Rule 12 exactly — and the mutation suite proved
    it: gutting both detectors to `return []` survived the entire gate.

    So the detectors get positives, built by hand. Without this, every
    completeness assertion above is decoration.
    """
    st = store.MemoryStore()
    text, _ = compress_json(bulky(), store=st)

    # The premise, and it must FAIL rather than raise. The first version called
    # render.parse straight away, and with the store switched off `bulky()` is
    # not a #margin document at all — its bodies are long and unique, so the
    # table saves almost nothing over compact JSON and falls below
    # MIN_TABLE_SAVING. parse rightly refused, this function raised, and a
    # *crashed* gate reports no failure at all: the mutation suite read that as
    # "nothing noticed" and two mutations that were being caught correctly one
    # line further down were reported as survivors. A check that explodes is not
    # a check that fails.
    #
    # It is also the neatest demonstration of why the #store line is worth
    # asserting separately: without the store this payload has no table either.
    if not text.startswith(FORMAT_MARKER):
        return False
    table = render.parse(text)
    doc_id = table.get("store")
    if not doc_id:
        return False
    index = st.read_index(doc_id)
    if not index:
        return False

    # A missing object: the index still claims it, the store no longer has it.
    broken = dict(index)
    victim = sorted(broken)[0]
    without_object = store.MemoryStore()
    without_object.objects = {name: value for name, value in st.objects.items()
                              if name != broken[victim]}
    if store.missing_handles(broken, without_object) != [victim]:
        return False

    # An orphan: an index entry the document never references.
    with_orphan = dict(index)
    with_orphan["9999"] = with_orphan[victim]
    if store.orphaned_ids(with_orphan, store.used_ids(table)) != ["9999"]:
        return False

    # And an intact store must still come back clean, or the detectors are just
    # returning "broken" for everything.
    return (not store.missing_handles(index, st)
            and not store.orphaned_ids(index, store.used_ids(table)))


def abandoning_a_document_leaves_the_store_clean():
    """Stash cells, then discard the document, and write nothing.

    compress_json replaces cells with handles before it knows whether it will
    keep the table, so every path that abandons the document afterwards must
    leave the store as it found it. The random sweep cannot reach this: once bulk
    cells are stored the document is far smaller than the JSON it is compared
    against, so it never loses on MIN_TABLE_SAVING.

    It is reachable through the one comparison the caller controls —
    `original_text`. compress_json's promise is to hand back the original when
    the document does not beat it, so passing a tiny original exercises exactly
    the branch that discards a document with cells already stashed.
    """
    st = store.MemoryStore()
    text, notes = compress_json(bulky(), original_text="[]", store=st)
    if text != "[]" or not notes:
        return False  # the premise: this must actually have been abandoned
    return not st.objects and not st.indexes


def shrink_safe(payload):
    """shrink(), for the degrades-safely property."""
    return _shrink(payload, degrades_safely)


def shrink(payload):
    """Cut rows and keys while the failure survives, so the report is readable.

    A 6-row payload of awkward values says almost nothing about which value did
    it. Greedy removal is not minimal, but it reliably gets from "six rows of
    noise" to "two rows and one key", which is the difference between a report
    that gets debugged and one that gets ignored.
    """
    return _shrink(payload, round_trips)


def _shrink(payload, holds):
    """Greedily drop rows, then keys, while `holds` still reports a failure."""
    if not isinstance(payload, list):
        # A wrapped payload — the rows are buried in an envelope, and cutting
        # the envelope apart would change which array gets tabulated, so the
        # shrunk result would no longer reproduce the failure it came from.
        # Report it whole; the generator's envelopes are small.
        return payload

    changed = True
    while changed:
        changed = False

        for index in range(len(payload)):
            if len(payload) <= 2:
                break  # MIN_ROWS_TO_TABULATE — fewer rows stops tabulating at all
            candidate = payload[:index] + payload[index + 1:]
            if not holds(candidate):
                payload, changed = candidate, True
                break

        for key in {k for row in payload for k in row}:
            candidate = [{k: v for k, v in row.items() if k != key} for row in payload]
            if any(row for row in candidate) and not holds(candidate):
                payload, changed = candidate, True
                break

    return payload


def repeated(value, other, rows=8):
    """A payload big enough that the compressor actually builds and ships a table.

    Two rows of anything almost always lose to plain JSON on MIN_TABLE_SAVING, so
    a two-row regression case silently tests the fallback instead of the thing it
    was written to test. Eight rows of a repeating value clear the gate.
    """
    return [{"i": index, "a": value if index % 2 == 0 else other} for index in range(rows)]


# Every bug a hand-probe or this file has found, kept forever, each paired with
# what should happen — because "it round-tripped" is not the same claim as "the
# table was correct". A case that must tabulate and instead degrades has found a
# regression; a case that is allowed to degrade has found nothing.
def low_cardinality(values, rows=20):
    """Rows drawing one column from a small set — the #dict path.

    Long values on purpose: a dictionary of short ones correctly loses to
    writing them out, so a case built from "a"/"b" would test the gate refusing
    rather than the encoder working.
    """
    return [{"i": index, "a": values[index % len(values)]} for index in range(rows)]


_PAD = "PADDING VALUE PADDING VALUE PADDING"
# Long enough that a dictionary beats writing the value out. Needed because the
# interesting part of the two type cases below is a bare 0 or True, and a
# dictionary of bare scalars correctly loses — the first version of those cases
# round-tripped happily while building no dictionary at all, testing the table
# and not the encoder they were written for.

MUST_DICTIONARY = [
    # Rule 1, aimed at this encoding: compress_json falls back to plain JSON
    # whenever it cannot prove a table correct, so a completely broken #dict
    # still round-trips. These cases assert the LINE IS THERE. A case here that
    # merely round-trips has proved nothing.
    low_cardinality(["CONTRIBUTOR ACCESS LEVEL", "COLLABORATOR ACCESS LEVEL"]),
    low_cardinality([                                    # labels-shaped: nested objects
        [{"id": 196858374, "name": "CLA Signed", "colour": "e7e7e7", "default": False}],
        [{"id": 40929151, "name": "Type: Bug", "colour": "b60205", "default": False}],
    ]),
    # 0 and 0.0 in one column, True and 1 in another. Python says both pairs are
    # equal, so a dictionary built with a set or == would merge each pair and
    # hand every row after the first the wrong type — the bug #const shipped on
    # 2026-08 (Rule 9). Both entries must survive as separate list items.
    [{"i": index, "n": [[0, _PAD], [0.0, _PAD]][index % 2]} for index in range(20)],
    [{"i": index, "b": [[True, _PAD], [1, _PAD]][index % 2]} for index in range(20)],
    # Nullable dictionary column: null stays \N rather than becoming an entry,
    # or the header's `?` stops meaning anything.
    [{"i": index, "a": ["LONG VALUE ONE HERE", None, "LONG VALUE TWO HERE"][index % 3]}
     for index in range(20)],
]

MUST_NOT_DICTIONARY = [
    # Every value distinct: there is nothing to factor out, and a dictionary
    # would cost the values plus an index per row to save nothing.
    [{"i": index, "a": f"UNIQUE VALUE NUMBER {index} HERE"} for index in range(20)],
    # Repetitive but short. The dictionary would cost more than the cells, which
    # is the case MIN_DICT_SAVING exists to refuse — and the one a
    # distinct-count heuristic would have got wrong.
    [{"i": index, "a": ["x", "y"][index % 2]} for index in range(20)],
]

_BULK = ("A body long enough that moving it out of the document beats leaving "
         "it in, by more than MIN_STORE_SAVING tokens. Real issue bodies and "
         "post bodies are the shape this stands in for, and the bar is measured "
         "in src/measure_store.py rather than guessed at here. ") * 3
# Sized against the threshold rather than eyeballed: a case built from a short
# string would store nothing and quietly test the gate refusing, which is the
# mistake _PAD above exists to record for #dict.


def bulky(rows=8):
    """Rows with one cell well over the storing bar, and one well under."""
    return [{"i": index, "small": "x", "body": f"{_BULK} row {index}"}
            for index in range(rows)]


MUST_STORE = [
    # Rule 1, aimed at this encoding. compress_json falls back to plain JSON
    # whenever it cannot prove a table correct, and a store that silently wrote
    # nothing still round-trips perfectly — the cells would simply still be in
    # the document. These assert the #store LINE IS THERE. A case here that
    # merely round-trips has proved nothing at all.
    bulky(),
    # A bulk cell in a `json` column: the handle must be recognised before the
    # type dispatch, or decode_cell hands `\@0001` to json.loads.
    [{"i": index, "a": [{"text": _BULK, "n": index}] if index % 2 else 5}
     for index in range(8)],
    # A bulk cell in a nullable column, so \N and \@nnnn coexist.
    [{"i": index, "a": None if index % 3 == 0 else f"{_BULK} {index}"}
     for index in range(9)],
]
# There is deliberately no "identical bulk value in every row" case here. It was
# written, and it failed: #const factors a column that never varies out of the
# table entirely, so the store never sees a cell and no #store line is written.
# The same holds one step down — a column drawing on a few repeated long values
# is claimed by #dict. Within a single document, repeated bulk content is always
# taken by an existing rule before the store is reached, which is the store being
# measured after them working exactly as intended.
#
# So the store's dedup is not reachable inside one document, and testing it there
# would have been testing something that cannot happen. It is reachable across
# documents — the same issue body fetched on two different days — and that is
# what dedupes_across_documents() below checks instead.

MUST_NOT_STORE = [
    # Everything short. Storing here would cost a handle to save nothing, and
    # every cell would become a fetch the reader has to make to learn "x".
    [{"i": index, "a": ["x", "y"][index % 2]} for index in range(20)],
    # Repetitive AND long — but #dict gets there first, so by the time the store
    # sees these cells they are single-digit indices. This is the case that
    # proves the store is measured after the existing rules and does not
    # double-count what they already took.
    low_cardinality(["A LONG REPEATED VALUE THAT #dict SHOULD CLAIM FIRST",
                     "ANOTHER LONG REPEATED VALUE FOR #dict TO CLAIM"]),
]

MUST_TABULATE = [
    # The encoder has to get these right, not dodge them.
    # A literal value that looks exactly like a handle. It must survive as the
    # string it is, not be mistaken for a fetch — the ambiguity the `\@` marker
    # was chosen to make impossible (encode_cell doubles a leading backslash, so
    # this writes as `\\@0001` and cannot match `\@`).
    repeated("\\@0001", "z"),
    repeated("\\@", "z"),
    repeated(["\\N"], ["z"]),                      # one-element array reads as null
    repeated(["\\A"], ["z"]),                      # ... as empty array
    repeated(["\\E"], ["z"]),                      # ... as empty string
    repeated([], [1, 2]),                          # empty array vs absent key
    repeated(True, False),                         # bool is an int in Python
    repeated(3.0, 2.5),                            # float that prints like an int
    repeated("日本語 café 🎉", "x"),                # unicode through csv
    repeated([16582146, 16582152], [1, 2]),        # the delta-encoding path
    repeated([9, 1, 5], [3, 2]),                   # unsorted ints stay absolute
    repeated("a,b", 'c"d'),                        # csv quoting in values
    repeated("e\nf", "g\r\nh"),                    # newlines in values
    repeated({"b": {"c": 1}}, {"b": {"c": 2}}),    # depth-2 flattening
    # 0 and 0.0 in one column. Python says 0 == 0.0, so this collapsed into
    # #const and came back all-float while the round-trip check reported
    # "equal" — dict equality bottoms out in the same ==. Invisible to every
    # gate until same_json existed. Found by review, 2026-09-08.
    [{"id": i, "change": 0 if i % 2 else 0.0, "t": "x" * 12} for i in range(20)],
    # A nested object that is constant in every row: its dotted names land on
    # the #const line, never in the header, so the legend note explaining what
    # a.b means was not emitted at all.
    [{"id": i, "title": f"t{i}",
      "meta": {"source": "api-v2", "region": "us-east-1"}} for i in range(20)],
    # From the code review, 2026-09-08:
    repeated([], []),                              # no evidence: must not claim dints
    repeated([True], [1]),                         # [True] == [1] under plain ==
    [{"a": 1, "b": "x"} for _ in range(8)],        # every column constant: header [N]{}
    # Wrapper shapes, 2026-09-08. Records used to be findable only at depth 0-1,
    # so the first of these compressed by exactly 0%.
    {"data": {"items": [{"a": i, "b": "x" * 20} for i in range(8)], "total": 8}},
    {"r": {"data": {"items": [{"a": i, "b": "y" * 20} for i in range(8)]}}},
    {"users": [{"a": i, "b": "u" * 20} for i in range(8)], "posts": [{"c": 1}, {"c": 2}]},
    {"small": [{"z": 1}, {"z": 2}],                # must tabulate `big`, not `small`
     "big": [{"a": i, "b": "q" * 20} for i in range(10)]},
    # Record maps, 2026-09-08: a dict of like objects is a table whose first
    # column is the dict key. coingecko_prices.json went 0% -> 32.1% on this.
    {f"coin{i}": {"usd": i * 1.5, "eur": i * 1.3, "cap": i * 1000, "vol": i * 7}
     for i in range(8)},
    {"rates": {f"c{i}": {"buy": i * 1.1, "sell": i * 1.2, "mid": i * 1.15}
               for i in range(9)}, "base": "USD"},
    # A record whose own keys collide with the default key column name, so the
    # chosen name has to dodge them.
    {f"k{i}": {"_key": i, "_key2": i, "v": "x" * 12} for i in range(8)},
    # Past HEADER_REPEAT_EVERY, so the header appears mid-body and the parser
    # has to strip it back out. 2026-09-08.
    [{"a": i, "b": "y" * 15} for i in range(95)],
]

# A cell whose own text contains a line identical to the header. The repeated
# header is stripped by exact match on a whole line, and a quoted cell may
# legally contain newlines, so this is the one input that can defeat it.
#
# It belongs in MAY_DEGRADE, not MUST_TABULATE: the strip cannot tell the two
# apart, so the round-trip check fails and the payload correctly falls back to
# JSON. Data is never at risk; compression is. Accepted deliberately — the
# alternative is giving up repeated headers, which two cold reads say are worth
# more than this case costs.
#
# The value must VARY per row or the constant-column rule lifts it into #const
# and the cell never exists — which is how the first version of this case
# silently tested nothing.
HEADER_LOOKALIKE = [
    {"a": i, "b": f"before\n[95]{{a:int,b:str}}\nafter {i}"} for i in range(95)
]

MAY_DEGRADE = [
    # Keys the header syntax cannot express. Falling back to JSON is the correct
    # answer here — the requirement is that it degrades rather than crashes, and
    # that the data still survives exactly.
    [{"a,b": 1}, {"a,b": 2}],                      # comma in key vs header syntax
    [{"a\nb": 1}, {"a\nb": 2}],                    # newline in key
    [{"a.b": 1}, {"a.b": 2}],                      # dot in key vs flattening
    [{"a": {"b": {}}}, {"a": {"b": {}}}],          # empty dict flattens to no columns
    [{"a": {"b": {"c": 1}}}, {"i": 2}],            # absent nested parent
    [{"a": 5, "a.b": 6}, {"a": 7, "a.b": 8}],      # same key as value and parent
    # A dict of dynamic keys whose values are bare scalars is NOT a record map:
    # each record is one number, so there is no repeated key name to factor out
    # and a two-column key/value table costs more than the JSON it replaces.
    # Measured on exchangerates_usd.json: -1.3%. This is the line _is_record_map
    # holds, and the case that proves it holds it.
    {"rates": {f"c{i}": i * 1.5 for i in range(60)}, "base": "USD"},
    # Ragged objects under dynamic keys — a dict of unrelated things, not a
    # table. Tabulating it would give a wide sparse mess.
    {"a": {"x": 1}, "b": {"y": 2, "z": 3}, "c": {"w": 4}},
    HEADER_LOOKALIKE,
]


# Where the records must be found. Round-trip cannot check this: tabulating the
# wrong array still round-trips perfectly, it just compresses far less and puts
# the interesting data in #wrap as raw JSON.
EXPECTED_PATHS = [
    ([{"a": i, "b": "x" * 20} for i in range(8)], []),
    ({"hits": [{"a": i, "b": "x" * 20} for i in range(8)], "nbHits": 8}, ["hits"]),
    ({"data": {"items": [{"a": i, "b": "x" * 20} for i in range(8)], "total": 8}},
     ["data", "items"]),
    ({"r": {"data": {"items": [{"a": i, "b": "y" * 20} for i in range(8)]}}},
     ["r", "data", "items"]),
    # The largest candidate wins, not the first one dict ordering happens to
    # offer. `small` comes first and would have been chosen before 2026-09-08.
    ({"small": [{"z": 1}, {"z": 2}],
      "big": [{"a": i, "b": "q" * 20} for i in range(10)]}, ["big"]),
    # A ONE-row list is wider than the real records and used to win on cell
    # count — then compress.py rejected it for having one row and emitted plain
    # JSON without ever trying the runner-up. 0% on a payload that compresses by
    # 42.6% once the summary is gone. Found by review, 2026-09-08.
    ({"summary": [{f"k{i}": i for i in range(200)}],
      "items": [{"a": i, "b": "x" * 20} for i in range(30)]}, ["items"]),
]


def path_chosen(payload):
    """Which array the compressor actually tabulated, or None if it emitted JSON."""
    text, _ = compress_json(payload)
    if not text.startswith(FORMAT_MARKER):
        return None
    line = next((l for l in text.split("\n") if l.startswith("#path")), None)
    return json.loads(line[len("#path"):]) if line else []


def legend_explains_dotted_columns(payload):
    """If the document shows a dotted name anywhere, it must say what a dot means.

    The note was emitted only for varying columns, but flattening puts dotted
    names on the #const line just as readily — and a payload whose only nested
    object is constant printed #const{"meta.source":...} with no legend at all.
    The round-trip cannot see this: the data is fine, the explanation is missing.
    Found by review 2026-09-08; same family as type_claims_are_backed.
    """
    text, _ = compress_json(payload)
    if not text.startswith(FORMAT_MARKER):
        return True

    lines = text.split("\n")
    header = next(l for l in lines if l.startswith("["))
    const = next((l for l in lines if l.startswith("#const")), "")
    legend = next((l for l in lines if l.startswith(LEGEND_PREFIX)), "")

    shows_dotted = any(
        "." in spec.rsplit(":", 1)[0]
        for spec in header[header.index("{") + 1:header.rindex("}")].split(",")
        if spec
    ) or any("." in key for key in json.loads(const[len("#const"):] or "{}"))

    return not shows_dotted or "a.b" in legend


def legend_explains_keyed_column(payload):
    """If the document carries a #keyed line, the legend must name that column
    AND say it is not a field of the record.

    Returns None when the payload produced no #keyed line, so the caller can tell
    "passed" from "never applied" — Rule 2.

    The negation is the assertion, not decoration. Cold read #5 (2026-09-11) had
    the old wording in front of it, which named the column and stopped there, and
    reported _key as a thirteenth field of the ethereum record. Round-trip cannot
    see any of this: decompress strips the column either way, so the data is
    correct and the explanation is wrong (Rule 3, same family as
    legend_explains_dotted_columns and type_claims_are_backed).

    The column name is read out of the document rather than assumed to be `_key`:
    free_key_name dodges a record's own keys, so a payload whose records already
    carry `_key` and `_key2` is keyed on `_key3`, and a legend naming the wrong
    one is exactly the failure this is aimed at.

    That last claim is why the name is matched as `column {name} ` — the exact
    phrase render.legend_for emits — and not with `in`. A substring test is
    one-directional: with the document keyed on `_key`, a legend wrongly naming
    `_key3` still contains `_key`, so the check passes on the half of the failure
    it was named for. The `_key3` fixture happens to exercise the other half,
    which is how a substring test looked like it worked (review, 2026-09-12).
    """
    text, _ = compress_json(payload)
    if not text.startswith(FORMAT_MARKER):
        return None

    lines = text.split("\n")
    keyed = next((l for l in lines if l.startswith(render.KEYED_PREFIX)), None)
    if keyed is None:
        return None

    column = json.loads(keyed[len(render.KEYED_PREFIX):])
    legend = next((l for l in lines if l.startswith(LEGEND_PREFIX)), "")
    return f"column {column} " in legend and "NOT a field of the record" in legend


def type_claims_are_backed(payload):
    """No column may declare an array type it has no evidence for.

    A column of nothing but empty arrays satisfies "every element is an int"
    vacuously, and used to be declared `dints` — telling the reader that a
    column of Algolia `matchedWords` (strings) held delta-encoded integers. The
    round-trip cannot see this: every cell is the same either way. Only a check
    on the header itself can.
    """
    text, _ = compress_json(payload)
    if not text.startswith(FORMAT_MARKER):
        return True

    header = next(line for line in text.split("\n") if line.startswith("["))
    inner = header[header.index("{") + 1:header.rindex("}")]
    if not inner:
        return True

    # Follow the document's own #path rather than guessing where the rows are —
    # they can now sit several levels down inside the wrapper.
    rows = decompress(text)
    for key in path_chosen(payload) or []:
        rows = rows[key]

    dictionaries = render.parse(text).get("dictionaries") or {}

    for spec in inner.split(","):
        name, type_name = spec.rsplit(":", 1)
        type_name = type_name.rstrip("?")

        if type_name == "dict":
            # Same rule, aimed at the newer claim. `col:dict` tells the reader
            # that the integer in that cell stands for something on the #dict
            # line, so there had better be a list there, it had better hold more
            # than one thing — one entry is a constant wearing a disguise — and
            # every index had better point into it. A document that claimed
            # `dict` with no list would send a reader looking for a lookup table
            # that does not exist; one whose indices ran past the end would have
            # them read the wrong value with no way to notice.
            entries = dictionaries.get(name)
            if not entries or len(entries) < 2:
                return False
            continue

        if type_name not in ("ints", "dints", "strs"):
            continue
        values = [row[name] for row in rows if isinstance(row, dict) and name in row]
        if not any(values):
            return False  # declared an array encoding on zero elements

    # Nothing may sit on the #dict line without a column declaring it: an
    # orphaned entry is dead weight the reader has to read past, and it means
    # the header and the line disagree about what the document contains.
    declared = {spec.rsplit(":", 1)[0] for spec in inner.split(",")
                if spec.rsplit(":", 1)[1].rstrip("?") == "dict"}
    return declared == set(dictionaries)


def degrades_safely(payload):
    """Data survives exactly, and nothing raised. The table may be abandoned."""
    try:
        text, _ = compress_json(payload)
        return same_json(decompress(text), strip_boilerplate(payload))
    except Exception:
        return False


def main():
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else TRIALS
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else SEED
    rng = random.Random(seed)

    failures = []

    for index, payload in enumerate(MUST_TABULATE):
        if not round_trips(payload):
            failures.append(("MUST_TABULATE", index, payload))

    for index, payload in enumerate(MAY_DEGRADE):
        if not degrades_safely(payload):
            failures.append(("MAY_DEGRADE", index, payload))

    # The #keyed legend, checked on the fixed cases rather than the random sweep:
    # random_payload never builds a record map, so a check hung off the sweep
    # would pass while testing nothing (Rule 2).
    #
    # Three MUST_TABULATE cases are written as record maps and exactly ONE of
    # them reaches this check — the `_key3` fixture, whose records own `_key` and
    # `_key2`. The other two save 3.3% and -5.1%, below MIN_TABLE_SAVING, so they
    # fall back to plain JSON and emit no #keyed line at all; the printed
    # `(N keyed)` count is what says so out loud. Do not read the fixture list as
    # three-way redundancy here — it is one tripwire, which is why a run finding
    # zero is a failure rather than a skip (Rule 13's shape).
    #
    # (`MUST_TABULATE` asserts only round-trip, so two entries in a list of that
    # name silently do not tabulate. Pre-existing and left alone deliberately:
    # changing what MUST_TABULATE means is a bigger change than this one, and
    # Rule 6 is where it belongs.)
    keyed_cases = 0
    for index, payload in enumerate(MUST_TABULATE):
        verdict = legend_explains_keyed_column(payload)
        if verdict is None:
            continue
        keyed_cases += 1
        if not verdict:
            failures.append(("MUST_TABULATE (#keyed unexplained in the legend)",
                             index, payload))
    if keyed_cases == 0:
        failures.append(("MUST_TABULATE (no case produced a #keyed line at all)", 0, []))

    # Two claims per case, not one. "It round-tripped" is satisfied by the JSON
    # fallback, so each of these also has to show the #dict line it was written
    # to produce — and its opposite has to show the absence of one.
    for index, payload in enumerate(MUST_DICTIONARY):
        text, _ = compress_json(payload)
        if not round_trips(payload) or render.DICT_PREFIX not in text:
            failures.append(("MUST_DICTIONARY (no #dict line)", index, payload))

    for index, payload in enumerate(MUST_NOT_DICTIONARY):
        text, _ = compress_json(payload)
        if not round_trips(payload) or render.DICT_PREFIX in text:
            failures.append(("MUST_NOT_DICTIONARY (built one anyway)", index, payload))

    # Three claims per case. The #store line must be there (a store that wrote
    # nothing round-trips perfectly), the data must survive the trip, and the
    # index must be complete — which no round-trip can see.
    for index, payload in enumerate(MUST_STORE):
        text, _ = compress_json(payload, store=store.MemoryStore())
        if render.STORE_PREFIX not in text:
            failures.append(("MUST_STORE (no #store line)", index, payload))
        elif not store_round_trips(payload):
            failures.append(("MUST_STORE (did not round-trip)", index, payload))
        elif not store_index_is_complete(payload):
            failures.append(("MUST_STORE (index incomplete or orphaned)", index, payload))
        elif stored_document_refuses_to_decompress_alone(payload) is not True:
            failures.append(("MUST_STORE (decompressed without its store)", index, payload))

    for index, payload in enumerate(MUST_NOT_STORE):
        text, _ = compress_json(payload, store=store.MemoryStore())
        if render.STORE_PREFIX in text:
            failures.append(("MUST_NOT_STORE (stored anyway)", index, payload))

    if not dedupes_across_documents():
        failures.append(("STORE (no dedup across documents, or a document broke)", 0, []))

    if not file_store_survives_real_bytes():
        failures.append(("STORE (FileStore does not survive real bytes on disk)", 0, []))

    if not detects_a_broken_store():
        failures.append(("STORE (the completeness detectors do not detect)", 0, []))

    if not abandoning_a_document_leaves_the_store_clean():
        failures.append(("STORE (abandoned document left content behind)", 0, []))

    for index, (payload, expected) in enumerate(EXPECTED_PATHS):
        found = path_chosen(payload)
        if found != expected:
            failures.append((
                f"EXPECTED_PATHS (wanted {expected}, tabulated {found})", index, payload,
            ))

    # Sweep 1: keys the format can express. These must produce a working table,
    # so a silently-abandoned table is a failure.
    tabulated = 0
    dictionaried = 0
    stored = 0
    for _ in range(trials):
        payload = random_payload(rng, SAFE_KEYS)
        if not round_trips(payload):
            failures.append(("random/safe-keys", seed, shrink(payload)))
            break  # one good report beats a hundred variations of it
        if not type_claims_are_backed(payload):
            failures.append(("random/unbacked-type", seed, payload))
            break
        if not legend_explains_dotted_columns(payload):
            failures.append(("random/unexplained-dots", seed, payload))
            break
        document = compress_json(payload)[0]
        tabulated += document.startswith(FORMAT_MARKER)
        dictionaried += render.DICT_PREFIX in document

        # The store is exercised on the random sweep too, and its completeness
        # checked there — the fixed MUST_STORE cases are shapes someone thought
        # of, and Rule 2's whole point is that those are not the ones that break.
        if not store_index_is_complete(payload):
            failures.append(("random/store-index-incomplete", seed, payload))
            break
        stored += render.STORE_PREFIX in compress_json(payload, store=store.MemoryStore())[0]

    # Sweep 2: keys the format cannot express. Degrading is the correct answer;
    # crashing or losing data is not.
    for _ in range(trials // 4):
        payload = random_payload(rng, SAFE_KEYS + UNREPRESENTABLE_KEYS)
        if not degrades_safely(payload):
            failures.append(("random/awkward-keys", seed, shrink_safe(payload)))
            break

    fixed = (len(MUST_TABULATE) + len(MAY_DEGRADE)
             + len(MUST_DICTIONARY) + len(MUST_NOT_DICTIONARY)
             + len(MUST_STORE) + len(MUST_NOT_STORE))
    # The tabulated count is printed because a run where nothing tabulated would
    # pass while testing only json.dumps — a green result that means nothing.
    # If it ever reads 0, the generator is broken, not the compressor.
    print(f"property test: {fixed} fixed cases ({keyed_cases} keyed), "
          f"{trials} safe-key trials "
          f"({tabulated} built a table, {dictionaried} built a #dict, "
          f"{stored} built a #store), "
          f"{trials // 4} awkward-key trials, seed {seed}")
    if tabulated == 0:
        print("  WARNING: no trial built a table — the random sweep proved nothing")
    # Same reasoning one encoding down. Every other value family draws from a
    # wide range, so without the "few" family no generated column would repeat
    # often enough to earn a dictionary and the sweep would test the new encoder
    # zero times while reporting success.
    if dictionaried == 0:
        print("  WARNING: no trial built a #dict — the dictionary encoder is untested here")

    if not failures:
        print("0 failure(s)")
        return 0

    for source, marker, payload in failures:
        print(f"\nFAIL ({source} {marker}) — round-trip broken on:")
        print(f"  {json.dumps(payload)}")
        print("  Add it to FIXED_CASES once fixed.")
    print(f"\n{len(failures)} failure(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
