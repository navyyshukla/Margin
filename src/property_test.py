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

from compress import compress_json, strip_boilerplate
from render import FORMAT_MARKER
from decompress import decompress

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
    kind = rng.choice(["int", "str", "sorted_ints", "strs", "nested", "mixed"])

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
    return [
        {
            "i": index,
            **{k: gen() for k, gen in families.items() if rng.random() < 0.85},
        }
        for index in range(rng.randint(6, 20))
    ]


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
        return decompress(text) == strip_boilerplate(payload)
    except Exception:
        return False


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
MUST_TABULATE = [
    # The encoder has to get these right, not dodge them.
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
    # From the code review, 2026-09-08:
    repeated([], []),                              # no evidence: must not claim dints
    repeated([True], [1]),                         # [True] == [1] under plain ==
    [{"a": 1, "b": "x"} for _ in range(8)],        # every column constant: header [N]{}
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
]


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

    rows = decompress(text)
    if isinstance(rows, dict):
        rows = next(v for v in rows.values() if isinstance(v, list))

    for spec in inner.split(","):
        name, type_name = spec.rsplit(":", 1)
        if type_name.rstrip("?") not in ("ints", "dints", "strs"):
            continue
        values = [row[name] for row in rows if isinstance(row, dict) and name in row]
        if not any(values):
            return False  # declared an array encoding on zero elements
    return True


def degrades_safely(payload):
    """Data survives exactly, and nothing raised. The table may be abandoned."""
    try:
        text, _ = compress_json(payload)
        return decompress(text) == strip_boilerplate(payload)
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

    # Sweep 1: keys the format can express. These must produce a working table,
    # so a silently-abandoned table is a failure.
    tabulated = 0
    for _ in range(trials):
        payload = random_payload(rng, SAFE_KEYS)
        if not round_trips(payload):
            failures.append(("random/safe-keys", seed, shrink(payload)))
            break  # one good report beats a hundred variations of it
        if not type_claims_are_backed(payload):
            failures.append(("random/unbacked-type", seed, payload))
            break
        tabulated += compress_json(payload)[0].startswith(FORMAT_MARKER)

    # Sweep 2: keys the format cannot express. Degrading is the correct answer;
    # crashing or losing data is not.
    for _ in range(trials // 4):
        payload = random_payload(rng, SAFE_KEYS + UNREPRESENTABLE_KEYS)
        if not degrades_safely(payload):
            failures.append(("random/awkward-keys", seed, shrink_safe(payload)))
            break

    fixed = len(MUST_TABULATE) + len(MAY_DEGRADE)
    # The tabulated count is printed because a run where nothing tabulated would
    # pass while testing only json.dumps — a green result that means nothing.
    # If it ever reads 0, the generator is broken, not the compressor.
    print(f"property test: {fixed} fixed cases, {trials} safe-key trials "
          f"({tabulated} built a table), {trials // 4} awkward-key trials, seed {seed}")
    if tabulated == 0:
        print("  WARNING: no trial built a table — the random sweep proved nothing")

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
