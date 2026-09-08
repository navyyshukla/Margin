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

    for index, (payload, expected) in enumerate(EXPECTED_PATHS):
        found = path_chosen(payload)
        if found != expected:
            failures.append((
                f"EXPECTED_PATHS (wanted {expected}, tabulated {found})", index, payload,
            ))

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
        if not legend_explains_dotted_columns(payload):
            failures.append(("random/unexplained-dots", seed, payload))
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
