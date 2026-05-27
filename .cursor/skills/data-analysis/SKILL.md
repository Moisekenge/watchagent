---
name: data-analysis
description: Answer questions about WatchAgent's stored weather data and detected events — per-city statistics, cross-city comparisons, time-window summaries, trends, and event breakdowns. Use whenever the user asks "what does the collected data show", "compare the cities", "how many X events fired", or similar analytical questions about readings/events in the database.
---

# WatchAgent data-analysis skill

This skill queries the live WatchAgent database (the same Postgres the poller
fills, or any SQLite/Postgres URL you point it at) and returns a **structured
JSON answer**. It is the primary way to interrogate the collected dataset from
inside Cursor.

## How to run

**Simplest (always works) — inside the running stack**, which reaches the
database over Compose's internal network with no host-port concerns:

```bash
docker compose exec api python .cursor/skills/data-analysis/scripts/analyze.py overview
```

**From the host** (needs the deps installed locally):

```bash
python .cursor/skills/data-analysis/scripts/analyze.py <command> [options]
```

The script resolves the database in this order: `--database-url`, then the
`DATABASE_URL` environment variable, then a `127.0.0.1:5432` default that matches
the port Compose publishes. If you already run PostgreSQL locally on 5432 (so the
container's port is shadowed), use the in-container form above or pass an explicit
`--database-url`.

## Commands

| Command | Question it answers |
|---------|---------------------|
| `overview` | How much data do we have? Reading counts per city, latest timestamps, event counts by type and severity. |
| `city <Name> [--hours H]` | What is one city doing? Latest reading + robust temp/wind stats and its recent events over the window. |
| `compare [--hours H]` | How do the cities differ right now? Per-city average temperature over the window and the current warmest/coldest spread. |
| `events [--type T] [--severity S] [--city C] [--hours H] [--limit N]` | What fired, and why? Filtered event list with reasons. |
| `trend <field> <City> [--hours H]` | Which way is a field moving? First vs last value, per-hour slope, and direction over the window. |

`<field>` is one of `temperature_2m`, `apparent_temperature`, `wind_speed_10m`,
`precipitation`. All output is JSON on stdout, so it can be piped or parsed.

## Examples

```bash
python .cursor/skills/data-analysis/scripts/analyze.py overview
python .cursor/skills/data-analysis/scripts/analyze.py compare --hours 24
python .cursor/skills/data-analysis/scripts/analyze.py city Vancouver --hours 48
python .cursor/skills/data-analysis/scripts/analyze.py events --severity severe --hours 72
python .cursor/skills/data-analysis/scripts/analyze.py trend temperature_2m Ottawa --hours 12
```
