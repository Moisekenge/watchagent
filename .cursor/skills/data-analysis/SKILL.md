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

```bash
python .cursor/skills/data-analysis/scripts/analyze.py <command> [options]
```

The script resolves the database in this order: `--database-url`, then the
`DATABASE_URL` environment variable, then a localhost default
(`postgresql+psycopg2://watchagent:watchagent_local_dev@localhost:5432/watchagent`).
When using the Docker stack from the host, Compose maps Postgres to
`localhost:5432`, so the default works. You can also run it inside the stack:
`docker compose exec api python .cursor/skills/data-analysis/scripts/analyze.py overview`.

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
