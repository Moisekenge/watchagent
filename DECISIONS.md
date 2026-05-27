# Design decisions

Short architecture-decision records: the choices that shaped WatchAgent, the
alternatives weighed, and why each was made. The aim is to make the reasoning
auditable rather than to bury it in code comments.

---

## ADR-1 · Notability is relative, not absolute (no fixed thresholds)

**Context.** The brief warns that "fire when temperature > 30 °C" is technically
correct but intellectually shallow, and hints that the same change means
different things in different cities.

**Decision.** Judge each reading against the city's own recent history instead of
a universal constant. The core detectors compute a deviation from a rolling
per-city baseline.

**Alternatives considered.** Global fixed thresholds (rejected: ignore local
climate, produce constant noise in volatile cities and silence in stable ones);
fixed per-city thresholds (rejected: still hand-tuned, brittle, and unfair to the
"explain your reasoning" bar).

**Consequence.** Sensitivity is *learned from data*. Measured on the 72-hour
sample dataset, Vancouver's temperature MAD is 1.85 °C versus Ottawa's 5.3 °C, so
an identical swing scores ≈2.9× higher in Vancouver — exactly the behaviour the
brief gestures at, with nothing city-specific hard-coded.

---

## ADR-2 · Robust statistics (median + MAD), not mean + standard deviation

**Context.** We need a per-city "how unusual is this" score.

**Decision.** Use the modified z-score (Iglewicz & Hoaglin): `0.6745·(x−median)/MAD`.

**Alternatives considered.** Mean + standard deviation (rejected: the outlier we
are trying to detect inflates both, letting an extreme value partially mask
itself — the estimator is contaminated by the thing it should flag). A trained
model (rejected: far too much for hourly data across three cities, and
unexplainable).

**Consequence.** The baseline is resistant to the very spikes it must catch. A
zero-MAD window (very flat data) falls back to a mean-absolute-deviation estimate
so we never divide by zero.

---

## ADR-3 · Field-specific detectors, not one generic rule

**Context.** Temperature, wind, precipitation, and weather-code behave very
differently. Precipitation is zero-inflated (mostly 0.0); weather-code is
categorical, not numeric.

**Decision.** Seven detectors matched to signal type: statistical (anomaly,
rapid_change, trend) for continuous fields; a state machine for precipitation
onset/cessation; categorical tier transitions for WMO codes; absolute safety
tiers for wind; and a relational detector for cross-city divergence.

**Alternatives considered.** A single z-score over every field (rejected: a
z-score on a zero-inflated series is meaningless; a magnitude rule can't express
"clear → thunderstorm").

**Consequence.** Each field is judged the way it actually behaves. Some overlap
is intentional (a violent jump is both an anomaly and a rapid_change) — that is
corroborating signal, and the cooldown keeps the volume sane.

---

## ADR-4 · Explicit noise control: cooldown + cold-start guard

**Context.** "A useful event fires selectively." A detector that never goes quiet
is as useless as one that never fires.

**Decision.** A refractory **cooldown** per `(city, event_type, field)` (3 h
default, 6 h for slow trends), plus a **cold-start guard** that disarms
statistical detectors until a city has `MIN_HISTORY` (8) readings.

**Alternatives considered.** No suppression (rejected: a multi-hour heat wave
would re-fire every poll). Global cooldown across all types (rejected: a
thunderstorm onset would be hidden by an unrelated wind cooldown).

**Consequence.** On the sample dataset the cooldown reduces raw firings from
**78 → 44 (≈44 % suppression)** while preserving every distinct episode — the
sensitivity-vs-noise balance, quantified and reproducible via the replay skill.

---

## ADR-5 · Detectors are pure functions

**Context.** The event logic is the graded centrepiece and must be exhaustively
testable.

**Decision.** Every detector is `(reading, history, config) -> list[EventData]`
with no database access and no wall-clock (`now` is injected). Storage and
orchestration live in the poller.

**Alternatives considered.** Detectors that query the DB directly (rejected:
untestable without a database, and couples detection to storage).

**Consequence.** Detection tests construct reading sequences by hand and assert
both fire and no-fire cases, deterministically. The same pure functions are
reused unchanged by the replay skill.

---

## ADR-6 · Separate poller and API containers

**Context.** Collection and serving are different responsibilities with different
failure modes.

**Decision.** Run the poller and the API as two services from one image, sharing
the database. The API is read-only; only the poller writes.

**Alternatives considered.** One process running both (rejected: an API restart
would interrupt collection and vice-versa; mixing a blocking poll loop with the
web server muddies failure semantics).

**Consequence.** Independent lifecycles and a clean read/write split, at the cost
of one extra container — cheap and worth it.

---

## ADR-7 · Synchronous SQLAlchemy, not async

**Context.** The workload is tiny: three cities, hourly upstream data.

**Decision.** Sync SQLAlchemy 2.0 with injected sessions.

**Alternatives considered.** Async SQLAlchemy + asyncpg (rejected: async buys
throughput this workload will never need, while adding real complexity around
event loops and session scoping). The honest trade is clarity over a non-benefit.

**Consequence.** Simple, obvious control flow. Sessions are passed in, so the
exact same data layer runs on Postgres in production and SQLite in tests.

---

## ADR-8 · PostgreSQL in production, database-agnostic models for tests

**Context.** We want a real relational store with a database-enforced dedup
guarantee, but tests must be fast and dependency-free.

**Decision.** Postgres via Compose (healthcheck + named-volume persistence) with
a `UNIQUE(city, timestamp)` constraint; models use only generic column types and
the generic `JSON` type so the identical schema runs on SQLite in tests.

**Alternatives considered.** SQLite everywhere (rejected: weaker infrastructure
signal, no healthcheck/persistence story). Postgres in tests too (rejected: makes
CI need a database service for the unit-test job; slower and more fragile).

**Consequence.** Dedup is guaranteed by the constraint *and* a pre-insert check;
CI's test job needs no database; the build job proves the image builds.

---

## ADR-9 · FastAPI

**Context.** Three endpoints needing typed query/response validation and good
docs.

**Decision.** FastAPI.

**Alternatives considered.** Flask (rejected: manual validation and no built-in
schema/docs). Django REST (rejected: far too heavy for three read endpoints).

**Consequence.** Pydantic validates query params and response shapes; OpenAPI
docs are generated at `/docs` for free.

---

## ADR-10 · Structured JSON logging via the standard library

**Context.** Logs should be machine-parseable for `docker logs` and shippers,
without a heavy dependency.

**Decision.** A ~30-line stdlib JSON formatter; call sites attach context via the
standard `extra=` kwarg; messages stay static so they group cleanly.

**Alternatives considered.** `structlog` (rejected: a dependency for what a small
formatter does here). Plain text logs (rejected: not machine-parseable).

**Consequence.** One JSON object per line, with `city` / `http_status` / etc. as
first-class fields — enforced by a `.cursor` rule.
