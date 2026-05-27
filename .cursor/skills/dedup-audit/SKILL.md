---
name: dedup-audit
description: Scan stored readings for deduplication anomalies and collection gaps — duplicate (city, timestamp) rows that should be impossible, plus unusually long gaps between consecutive readings that indicate missed polls or upstream outages. Use to verify data integrity or investigate suspected duplicate/missing data.
---

# WatchAgent dedup-audit skill

Independently verifies the deduplication guarantee and surfaces collection
health issues. The `(city, timestamp)` UNIQUE constraint *should* make duplicates
impossible; this skill proves it from the data side and also flags gaps where the
poller appears to have missed an hourly reading.

## How to run

```bash
python .cursor/skills/dedup-audit/scripts/audit.py [--gap-minutes N]
```

Database resolution matches the other skills.

## What it reports (JSON)

- `duplicate_groups`: any `(city, timestamp)` appearing more than once — expected
  to be empty; a non-empty result means the dedup guarantee was violated.
- `per_city`: reading count, time span, and number of detected gaps per city.
- `gaps`: consecutive readings spaced more than `--gap-minutes` apart (default
  90; readings are hourly, so anything beyond ~90 min is a missed poll/outage).

```bash
python .cursor/skills/dedup-audit/scripts/audit.py
python .cursor/skills/dedup-audit/scripts/audit.py --gap-minutes 120
```
