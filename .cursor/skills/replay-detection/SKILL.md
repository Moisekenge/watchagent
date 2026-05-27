---
name: replay-detection
description: Replay the most recent stored readings back through the event-detection engine to see exactly what would fire under the current (or an overridden) configuration. Use when tuning thresholds, debugging why an event did or didn't fire, or estimating event volume / noise before changing detection logic.
---

# WatchAgent detection-replay skill

Re-runs `detect_events` over already-collected readings, in chronological order
per city, rebuilding history exactly as the live poller would. This is the tool
for answering "would this threshold over-fire?" without waiting for live weather.

## How to run

```bash
python .cursor/skills/replay-detection/scripts/replay.py [options]
```

Database resolution is identical to the data-analysis skill (`--database-url`,
then `DATABASE_URL`, then the localhost default).

## Options

| Option | Meaning |
|--------|---------|
| `--limit N` | Replay only the last N readings per city (default: all). |
| `--window N` | History window passed to the detectors (default: `ROLLING_WINDOW`). |
| `--no-cooldown` | Disable the refractory filter to see *raw candidate* volume — the unfiltered firing rate, useful for noise analysis. |
| `--city Name` | Restrict to one city. |

## Output

JSON summarising what fired: totals, a breakdown by event type and by city, and
a sample of event reasons. Compare the run with and without `--no-cooldown` to
quantify how much noise the cooldown layer is actually suppressing.

```bash
python .cursor/skills/replay-detection/scripts/replay.py --limit 50
python .cursor/skills/replay-detection/scripts/replay.py --no-cooldown --city Ottawa
```

Note: cross-city peers are reconstructed as each other city's latest reading at
or before the reading being replayed — a close approximation of live ordering.
