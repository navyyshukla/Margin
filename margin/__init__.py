"""margin — compress a JSON API response on its way into an LLM prompt.

A package, as of 2026-10-12, and `bin/margin` is gone. That wrapper's own
comment declined packaging because src/ was flat modules importing each other
by bare name, so a console script would have meant either restructuring every
file the harness guards or installing `table` and `render` into site-packages
under names anything could collide with.

That argument held while nothing was packaged. Once `pip install margin` is the
goal it inverts: the restructuring is no longer a cost paid for nothing, it is
the thing being bought. The seventeen harness files did NOT move — they import
this package by name — so the rename touched ten files, not twenty-seven.

Nothing is exported here on purpose. The entry point is margin.cli:main.
"""
