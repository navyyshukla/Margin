#!/usr/bin/env bash
# Refetch the eight sample payloads into data/samples/.
#
# WHY THIS EXISTS
#
# data/samples/ is gitignored (real third-party API responses; see .gitignore),
# so a clone has none of them and cannot run src/eval_harness.py at all — five
# of the eight gates still work, the answer-quality one does not. Worse, until
# this file existed the eight source URLs were written down nowhere: the
# payloads were reproducible only by whoever remembered the query strings.
# They are reconstructed here from the payloads themselves — the Algolia
# `params` field, CoinGecko's own key names, Open-Meteo's echoed coordinates
# and units — and each one was re-fetched and compared against the local file
# before this script was committed.
#
# WHAT IT DOES NOT PROMISE
#
# These are LIVE endpoints. A fresh fetch will not reproduce
# 121,569 -> 71,034 tokens (41.6%) exactly, and it is not meant to: GitHub's
# thirty newest issues are different issues, crypto prices move every minute,
# and Open-Meteo returns the next seven days rather than 2026-09-08..14.
#
# What survives is the SHAPE, which is what Margin compresses and what
# docs/shapes.md is actually about: a bare list of records, records under
# `hits`, a record map, an already-columnar payload, a map of scalars. The
# frozen figures stay in docs/shapes.md; this script reproduces the method,
# not the decimals.
#
# Measured rather than hoped for — this script run into a scratch directory on
# 2026-09-12, four days after the originals were fetched, each result piped
# through src/cli.py:
#
#   jsonplaceholder_posts, pokeapi_ditto, graphql_countries   byte-identical
#   hn_stories 43.0%, graphql 34.4%, coingecko 29.6%,
#     jsonplaceholder 26.2%, pokeapi 5.5%                     unmoved
#   openmeteo, exchangerates                                  still unchanged
#   github_issues 55.3% -> 58.7%                              thirty other issues
#
# So the only figure that moved is the one whose content is genuinely new, and
# the two 0.0% payloads stayed 0.0% — that one is a statement about the shape
# and should not move at all.
#
# The eval questions in src/checks_*.py are written against the payloads
# fetched on 2026-09-07..08, and some assert specific values. Run against the
# refetch above, src/eval_harness.py exits 0 on seven of the eight and 1 on
# github_issues.json only — which is correct behaviour, not a regression: that
# payload's questions name particular issues and thirty new issues do not have
# them. hn_stories and coingecko_prices pass despite being live, because their
# questions ask about structure rather than about which story or what price.
#
# Guessing which ones would fail got this wrong — the first draft of this
# comment named hn_stories too (Rule 5: re-measure, do not remember).
#
# Usage:  bash scripts/fetch_samples.sh  [outdir]
#         outdir defaults to data/samples/

set -uo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
outdir="${1:-$repo_root/data/samples}"
mkdir -p "$outdir"

command -v curl >/dev/null 2>&1 || { echo "curl not found" >&2; exit 1; }

failed=0

# Announced per payload rather than silently: a partial sample set makes
# eval_harness.py report on fewer payloads than you think it did, which is the
# kind of quiet shortfall .claude/hooks/run_eval.sh counts samples to avoid.
fetch() {
  local name="$1"; shift
  local dest="$outdir/$name.json"
  if curl -sSfL --max-time 30 "$@" -o "$dest.tmp"; then
    mv "$dest.tmp" "$dest"
    echo "  ok   $name.json ($(wc -c <"$dest" | tr -d ' ') bytes)"
  else
    rm -f "$dest.tmp"
    echo "  FAIL $name.json" >&2
    failed=$((failed + 1))
  fi
}

echo "fetching into $outdir"

# Bare list of records. The original sample is react/react, not the
# python/cpython in README.md's example command — that one is an illustration
# of the pipe, not of this payload.
fetch github_issues \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/react/react/issues?state=open&per_page=30"

# Records under `hits`. The query string is the `params` field of the stored
# payload, verbatim.
fetch hn_stories \
  "https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage=30&advancedSyntax=true&analyticsTags=backend"

# Record map: eight coin ids, three currencies, each with market cap, 24h
# volume and 24h change — which is where the twelve keys per record come from.
fetch coingecko_prices \
  "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,cardano,ripple,dogecoin,polkadot,chainlink&vs_currencies=usd,eur,gbp&include_market_cap=true&include_24hr_vol=true&include_24hr_change=true"

# Map of scalars — the payload that exists in this set specifically to hold
# the "0.0% is the correct answer" line in docs/shapes.md.
fetch exchangerates_usd \
  "https://open.er-api.com/v6/latest/USD"

# 100 flat records, and the only endpoint here that is guaranteed identical
# every time: JSONPlaceholder serves fixed fixture data.
fetch jsonplaceholder_posts \
  "https://jsonplaceholder.typicode.com/posts"

# One deep object. Stable — Ditto is still Pokémon 132.
fetch pokeapi_ditto \
  "https://pokeapi.co/api/v2/pokemon/ditto"

# Already columnar: four parallel arrays of 168 hourly values. Berlin, GMT,
# the three variables the stored payload carries.
fetch openmeteo_forecast \
  "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m&timezone=GMT&forecast_days=7"

# Records under data.countries. A GraphQL POST, so it is the one that cannot
# be a bare URL. Seven fields, matching the stored payload's keys.
gql_query='{"query":"{ countries { code name capital emoji currency continent { name } languages { code name } } }"}'
if curl -sSfL --max-time 30 \
     -H "Content-Type: application/json" \
     -d "$gql_query" \
     "https://countries.trevorblades.com/graphql" \
     -o "$outdir/graphql_countries.json.tmp"; then
  mv "$outdir/graphql_countries.json.tmp" "$outdir/graphql_countries.json"
  echo "  ok   graphql_countries.json ($(wc -c <"$outdir/graphql_countries.json" | tr -d ' ') bytes)"
else
  rm -f "$outdir/graphql_countries.json.tmp"
  echo "  FAIL graphql_countries.json" >&2
  failed=$((failed + 1))
fi

echo
if [ "$failed" -gt 0 ]; then
  echo "$failed of 8 failed. A missing payload is not a regression in Margin —" >&2
  echo "it is an API being down, rate-limited, or moved. Re-run, or fetch that" >&2
  echo "one by hand; the URL is above the fetch call." >&2
  exit 1
fi

echo "all 8 fetched. Measure them with:"
echo "    .venv/bin/python src/measure_tokens.py"
echo "and read docs/shapes.md for what the numbers should look like."
