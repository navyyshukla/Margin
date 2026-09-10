# docs/ — which file answers which question

The index. `CLAUDE.md` and the root `README.md` point here rather than each
keeping their own copy of this mapping.

| Question | File |
|---|---|
| What do I have to do before changing the format, a threshold or a hook? | the **`harness` skill** (`.claude/skills/harness/SKILL.md`) — the procedure |
| Why does that rule exist? What failure bought it? | [`harness.md`](harness.md) — the record, 15 rules, cited by number from 27 places outside itself |
| Which API shapes are handled, and why are two of them 0%? | [`shapes.md`](shapes.md) |
| Where did this number come from? | [`thresholds.md`](thresholds.md) |
| What does `margin` exit, and what does the pipe guarantee? | [`cli.md`](cli.md) |
| How does the store work, and how does a model reach it? | [`store.md`](store.md) |
| How do I run a cold read, and what have they found? | [`cold-reads.md`](cold-reads.md) |
| What did one particular cold read find? | `cold-reads/YYYY-MM-DD.md` — [#1](cold-reads/2026-09-08.md), [#2](cold-reads/2026-09-08b.md), [#3](cold-reads/2026-09-09.md), [#4](cold-reads/2026-09-10.md) |
| What is done, and what is next? | [`status.md`](status.md) |

## The split, and why

**Procedure lives in the skill; provenance lives here.** The skill loads on
demand and says what to do; `harness.md` holds the failure behind each rule and
is read when you need to know *why*. Anthropic's own trim guidance keeps
rationale and pitfalls rather than discarding them — but they are paid for once,
on demand, not re-read into every session.

**`CLAUDE.md` keeps only what cannot be discovered from the repo.** It arrives as
a user message, not a system prompt — context, not enforced configuration — so
anything that must happen *every* time is a hook, not a sentence.

## The house style, taken from `thresholds.md`

It is the model the rest of these should follow: **every number carries the story
of how it was got, including the times it was wrong.** `MIN_TABLE_SAVING` records
being wrong twice — a padded baseline, and a value set on two payloads that threw
away a real win on the eighth. That is not sentiment; a rule whose reason is
forgotten is a rule someone deletes, and a number whose derivation is forgotten
is a number someone copies from a blog post.
