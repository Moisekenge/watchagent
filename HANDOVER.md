# WatchAgent — Handover Guide

A practical guide for anyone **operating, maintaining, or extending** WatchAgent.
It is the operational companion to the deeper docs: the [README](README.md)
(overview, API reference, event-detection design), [ARCHITECTURE.md](ARCHITECTURE.md)
(diagrams), and [DECISIONS.md](DECISIONS.md) (why each choice was made).

WatchAgent polls live weather for **Ottawa, Toronto, and Vancouver**, decides
when a reading is genuinely *notable*, and exposes the readings and detected
events over an HTTP API. The interesting part is the detection layer, which
calibrates to each city's own behaviour instead of using hard-coded thresholds.

---

## 1. Status at handover

| Deliverable | State |
|---|---|
| Poller → storage → API stack | ✅ runs via `docker compose up --build` |
| Three endpoints (`/health`, `/readings`, `/events`) | ✅ contracts implemented |
| Deduplication on `(city, timestamp)` | ✅ DB constraint + pre-insert check |
| Event detection (7 detectors + cooldown) | ✅ pure, unit-tested |
| Cursor setup (5 rules, 2 agents, 3 skills) | ✅ committed under `.cursor/` |
| Unit tests | ✅ 29 passing (`pytest`) |
| CI (lint+tests, docker build) | ✅ green on `main` |
| DB persistence across restarts | ✅ `pgdata` named volume |

**Repo:** <https://github.com/Moisekenge/watchagent> ·
**CI:** [GitHub Actions](https://github.com/Moisekenge/watchagent/actions/workflows/ci.yml)

---

## 2. How to run it (TL;DR)

Prerequisites: **Docker Desktop running** + **Git**. Nothing else.

### Terminal

```bash
git clone https://github.com/Moisekenge/watchagent.git
cd watchagent
cp .env.example .env              # local dev defaults — no real secrets
docker compose up --build         # API on http://localhost:8000

# in a second terminal:
curl http://localhost:8000/health # → {"status":"ok","readings_stored":...,"events_stored":...}
```

The stack runs in Linux containers, so it behaves identically on macOS, Linux,
and Windows — only the host commands differ (`cp` vs `copy`, `curl` vs
`curl.exe`, venv activation). The per-OS command table is in
[README ▸ Running on macOS, Linux, and Windows](README.md#running-on-macos-linux-and-windows).

For the full guided walkthrough (seed sample data, run every skill, prove
persistence, and a no-Docker venv path), see
[README ▸ For reviewers — step-by-step](README.md#for-reviewers--step-by-step).

### With an AI agent (Cursor / Claude Code)

Open the repo in Cursor or Claude Code and instruct the agent in plain language —
it runs the same commands and reads the skill output back. The skills carry their
own `SKILL.md`, so the agent picks the right tool. Example:

```text
Start the stack with docker compose, wait for /health to be ok, seed the demo
dataset, then run the data-analysis skill and tell me which city is warmest and
how many events of each type fired.
```

---

## 3. Repository map

```
watchagent/
├── app/
│   ├── config.py          # env Settings + DetectionConfig (all thresholds)
│   ├── domain.py          # ReadingData / EventData dataclasses + enums
│   ├── models.py          # SQLAlchemy ORM; UNIQUE(city,timestamp) dedup
│   ├── db.py              # engine, sessions, init_db (Postgres + SQLite)
│   ├── repository.py      # ALL database access; ORM⇄domain conversions
│   ├── weather_client.py  # Open-Meteo client (UTC, timeout, retry/backoff)
│   ├── poller.py          # the collection loop (separate process/container)
│   ├── main.py            # FastAPI app: /health /readings /events
│   ├── logging_config.py  # single-line JSON logging
│   └── detection/
│       ├── baselines.py   # median, MAD, modified z-score
│       ├── wmo.py         # WMO code → description + severity tier
│       ├── rules.py       # the seven detectors (pure functions)
│       └── detector.py    # orchestrator + cooldown / de-dup of events
├── tests/                 # dedup, detection (fire/no-fire), API, client
├── scripts/               # generate_demo_data.py (reproducible dataset)
├── .cursor/               # rules · agents · skills  (graded deliverable)
├── .github/workflows/ci.yml
├── Dockerfile             # one image, two roles (api + poller)
├── docker-compose.yml     # db + poller + api
└── *.md                   # README, ARCHITECTURE, DECISIONS, HANDOVER
```

**Layering rule of thumb:** only `repository.py`, `models.py`, and `db.py` import
SQLAlchemy. The detection engine speaks only in `ReadingData` / `EventData`
dataclasses, which is what keeps it pure and unit-testable.

---

## 4. Configuration & environment variables

All operational config is environment-driven (12-factor). Copy `.env.example` to
`.env`; the defaults are safe local-dev values, **not secrets**. Never commit a
real `.env` — only `.env.example` is tracked.

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `watchagent` / `watchagent_local_dev` / `watchagent` | Credentials for the `db` container |
| `DATABASE_URL` | `postgresql+psycopg2://…@db:5432/watchagent` | App connection string (host `db` = Compose service) |
| `POLL_INTERVAL_SECONDS` | `300` | How often the poller fetches each city (Open-Meteo refreshes hourly) |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `ROLLING_WINDOW` | `48` | Readings retained per city for baselines |
| `MIN_HISTORY` | `8` | Readings before statistical detectors arm |
| `ANOMALY_Z` | `3.5` | Modified z-score cutoff for anomalies |
| `COOLDOWN_HOURS` | `3` | Refractory period per `(city, event_type, field)` |
| `CROSS_CITY_SPREAD_C` | `18` | Inter-city temperature spread treated as notable |

The cities themselves are fixed in code ([`app/config.py`](app/config.py), `CITIES`)
— they define the system's identity, not its deployment.

---

## 5. Operations runbook

All commands run from the repo root with the stack defined in `docker-compose.yml`.

```bash
docker compose up --build -d        # build + start in the background
docker compose ps                   # service status & health
docker compose logs -f poller       # follow poller logs (JSON, one line each)
docker compose logs -f api          # follow API logs
docker compose restart              # restart all services (data persists)
docker compose down                 # stop & remove containers (KEEPS data)
docker compose down -v              # stop & WIPE the database volume
```

**Data:**

```bash
# Seed the reproducible 72-hour sample dataset (instant rich events).
# --reset clears existing rows first. Resolves DATABASE_URL inside the container.
docker compose exec api python scripts/generate_demo_data.py --reset

# Verify persistence: counts are unchanged after a restart.
curl http://localhost:8000/health && docker compose restart && \
  sleep 5 && curl http://localhost:8000/health
```

**Querying the data (the Cursor skills).** Run **inside the stack** — the DB port
is intentionally not published to the host, so this avoids any host-port clash
with a local Postgres:

```bash
docker compose exec api python .cursor/skills/data-analysis/scripts/analyze.py overview
docker compose exec api python .cursor/skills/data-analysis/scripts/analyze.py compare
docker compose exec api python .cursor/skills/data-analysis/scripts/analyze.py events --severity severe
docker compose exec api python .cursor/skills/replay-detection/scripts/replay.py
docker compose exec api python .cursor/skills/dedup-audit/scripts/audit.py
```

Each skill also accepts `--database-url` (e.g. a SQLite file) and falls back to
`$DATABASE_URL`. See each skill's `SKILL.md` for its full command set.

---

## 6. Tests & CI

```bash
pip install -r requirements-dev.txt
pytest -q                 # 29 tests; mocked weather API + in-memory SQLite
ruff check app tests
```

Tests never touch the network or a real database, so they run identically in CI.
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push/PR to
`main` with two jobs: **lint+tests** and **docker build** (proves the image
builds with no API keys). Keep both green — a red pipeline is a submission
disqualifier.

Coverage map: `test_dedup.py` (same reading twice → one row), `test_detection.py`
(each detector fires on a triggering sequence and stays silent on a near-miss —
the most important tests), `test_api.py` (endpoint shapes/ordering/filters),
`test_weather_client.py` (UTC normalization, retry-then-raise).

---

## 7. The Cursor environment (graded)

Everything is under [`.cursor/`](.cursor/) and is specific to this codebase. Full
descriptions are in [README ▸ Cursor setup](README.md#cursor-setup).

- **Rules** ([`.cursor/rules/`](.cursor/rules/)) — five active conventions
  (poller resilience, the event-record contract, storage/dedup, structured
  logging, testing). They encode real decisions, e.g. *"a failed fetch logs city
  + http_status + attempt at WARNING and never raises out of `run_cycle`."*
- **Agents** ([`.cursor/agents/`](.cursor/agents/)) — two scoped, read-only code
  reviewers: `event-detection-reviewer` (logic under `app/detection/`) and
  `data-layer-reviewer` (SQLite↔Postgres portability + the dedup guarantee).
- **Skills** ([`.cursor/skills/`](.cursor/skills/)) — three executable tools:
  `data-analysis` (the graded one — queries the DB, returns structured JSON),
  `replay-detection` (replays stored readings through the detector), and
  `dedup-audit` (verifies the dedup guarantee + flags gaps).

---

## 8. Event detection in one screen

Notability is **relative to context**, so most detectors compare a new reading to
that city's own recent history using the **robust modified z-score** (median +
MAD), which resists the very outliers it is meant to catch. The seven detectors
are layered by signal type:

| Detector | Fires when |
|---|---|
| `anomaly` | a field is a robust-z outlier vs the city's baseline |
| `rapid_change` | step vs previous reading is large absolutely *and* statistically |
| `trend` | a monotonic run whose cumulative move is large |
| `precip_onset` / `precip_cessation` | dry→wet / wet→dry (state machine — precip is zero-inflated) |
| `condition_change` | WMO weather-code tier transition (e.g. clear → thunderstorm) |
| `high_wind` | wind newly crosses an absolute safety tier (strong/gale) |
| `cross_city_divergence` | temperature spread across the cities is extreme |

A per-`(city, event_type, field)` **cooldown** turns raw candidates into a
selective stream (≈44% suppression on the sample dataset). Every stored event
records what / where / when / why. Full rationale + reproducible tuning numbers:
[README ▸ Event detection design](README.md#event-detection-design) and
[DECISIONS.md](DECISIONS.md).

---

## 9. Extending it

- **Add a new event type:** add a pure detector to
  [`app/detection/rules.py`](app/detection/rules.py), a member to `EventType` in
  [`app/domain.py`](app/domain.py), call it from `detect_events` in
  [`app/detection/detector.py`](app/detection/detector.py) (and give it a branch
  in `_cooldown_hours` if it fires on a different cadence), then add a **fire and
  no-fire** test in `tests/test_detection.py`. This sequence is the contract in
  [`.cursor/rules/event-record-contract.mdc`](.cursor/rules/event-record-contract.mdc).
- **Add an API endpoint:** route in [`app/main.py`](app/main.py), response model
  in [`app/schemas.py`](app/schemas.py), query in
  [`app/repository.py`](app/repository.py) (keep all SQL in the repository).
- **Add a skill:** create `.cursor/skills/<name>/SKILL.md` + `scripts/<name>.py`
  following an existing skill (insert the repo root on `sys.path`, resolve the DB
  from `--database-url` → `$DATABASE_URL`, print JSON). `.cursor/` is already
  copied into the image, so no Dockerfile change is needed.
- **Change a threshold:** edit defaults in `DetectionConfig`
  ([`app/config.py`](app/config.py)) or override via env (section 4), then update
  the affected detection tests in the same change.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `docker compose up` fails on a fresh clone | Run `cp .env.example .env` first (Windows: `copy …` / `Copy-Item …`) — Compose needs `.env` for the DB credentials. |
| `Cannot connect to the Docker daemon` | Docker Desktop isn't running (macOS/Windows), or `sudo systemctl start docker` (Linux); on Linux you may also need `sudo` or `docker`-group membership. |
| `curl` behaves oddly on Windows | In PowerShell `curl` aliases `Invoke-WebRequest`. Use `curl.exe`, `Invoke-RestMethod`, or just open `/docs` in a browser. Quote URLs containing `&`. |
| Port `8000` already in use | Another process owns it; change the `api` mapping in `docker-compose.yml`, e.g. `ports: ["8080:8000"]`. |
| A skill run from the **host** can't reach the DB | The DB port isn't published by design. Run skills **inside the stack** (`docker compose exec api …`) or pass `--database-url`. |
| `/events` is empty / sparse | Open-Meteo refreshes hourly, so live events accrue slowly. Seed the demo dataset (section 5) to see every event type immediately. |
| Time-windowed skill commands look empty | Use a window that spans your data, e.g. `analyze.py city Ottawa --hours 72`. The demo dataset is anchored to end at the current hour so defaults work. |

---

## 11. Roadmap (next owners)

Deliberately out of scope for the brief; natural next steps, in priority order,
are in [README ▸ What I'd do with more time](README.md#what-id-do-with-more-time):
learned baselines from longer history, alerting + a `/metrics` endpoint, an
`/events/{id}` drill-down, historical backfill + TimescaleDB, and a
precision/recall evaluation harness on top of the replay skill.
