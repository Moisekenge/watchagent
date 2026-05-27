# Architecture & diagrams

End-to-end visual documentation of WatchAgent: how the pieces fit, how a reading
flows from the upstream API to a stored event, the data model, and how this would
run and scale in the cloud.

> **Interactive viewing.** All diagrams below are [Mermaid](https://mermaid.js.org/)
> and render directly on GitHub. To explore one interactively (pan, zoom, edit),
> paste it into <https://mermaid.live>. The **live, clickable API** is the Swagger
> UI the service itself serves at <http://localhost:8000/docs>. Under each diagram
> is a *Jump to source* list linking to the code it describes.

## Contents
- [1. System context](#1-system-context)
- [2. Container view (runtime)](#2-container-view-runtime)
- [3. Component view (inside the image)](#3-component-view-inside-the-image)
- [4. Sequence — a poll cycle (end to end)](#4-sequence--a-poll-cycle-end-to-end)
- [5. Sequence — an API request](#5-sequence--an-api-request)
- [6. Detection decision flow](#6-detection-decision-flow)
- [7. Precipitation state machine](#7-precipitation-state-machine)
- [8. Data model (ER)](#8-data-model-er)
- [9. Cloud deployment (AWS)](#9-cloud-deployment-aws)
- [10. How I would scale it](#10-how-i-would-scale-it)

---

## 1. System context

Who and what the system talks to.

```mermaid
flowchart LR
    operator([Operator / reviewer]):::actor
    consumer([API consumer / dashboard]):::actor
    subgraph WA[WatchAgent]
        svc[Weather monitor\n+ event detector]
    end
    om[(Open-Meteo API\nfree, no key, hourly)]:::ext

    operator -->|reads events & readings| svc
    consumer -->|HTTP GET /health /readings /events| svc
    svc -->|polls current conditions| om

    classDef actor fill:#dbeafe,stroke:#3b82f6,color:#1e3a8a;
    classDef ext fill:#f3e8ff,stroke:#a855f7,color:#581c87;
```

---

## 2. Container view (runtime)

Three processes, one image, one database. The poller writes; the API reads.

```mermaid
flowchart TD
    om[(Open-Meteo API)]:::ext

    subgraph compose[docker compose stack]
        poller["poller service\npython -m app.poller\n(no inbound port)"]
        api["api service\nuvicorn app.main:app\n:8000"]
        db[("postgres:17\nreadings, events\nnamed volume: pgdata")]
    end

    consumer([client]) -->|:8000| api
    poller -->|fetch hourly| om
    poller -->|"INSERT (dedup) + events"| db
    api -->|"SELECT (read-only)"| db

    classDef ext fill:#f3e8ff,stroke:#a855f7,color:#581c87;
```

*Why separate poller and API containers, and why the DB port is not published —
see [DECISIONS.md](DECISIONS.md) (ADR-6, ADR-8).*

**Jump to source:** [app/poller.py](app/poller.py) · [app/main.py](app/main.py) · [docker-compose.yml](docker-compose.yml)

---

## 3. Component view (inside the image)

The same code powers both services; each entrypoint uses the slices it needs.
Only the repository touches SQLAlchemy; the detection engine is pure.

```mermaid
flowchart TB
    subgraph entry[Entrypoints]
        P[poller.py\ncollection loop]
        A[main.py\nFastAPI routes]
    end
    subgraph core[Core]
        WC[weather_client.py\nOpen-Meteo + retry]
        REPO[repository.py\nDB access + dedup\nORM ⇄ domain]
        DET[detection/*\npure detectors + cooldown]
        DOM[domain.py\nReadingData / EventData]
        CFG[config.py\nSettings + DetectionConfig]
        LOG[logging_config.py\nJSON logs]
    end
    DB[(PostgreSQL)]

    P --> WC
    P --> REPO
    P --> DET
    A --> REPO
    REPO --> DB
    WC --> DOM
    DET --> DOM
    REPO --> DOM
    P --> CFG
    A --> CFG
    DET --> CFG
```

**Jump to source:** [app/weather_client.py](app/weather_client.py) · [app/repository.py](app/repository.py) · [app/detection/](app/detection/) · [app/domain.py](app/domain.py) · [app/config.py](app/config.py)

---

## 4. Sequence — a poll cycle (end to end)

What happens each interval, for each city. Note the dedup short-circuit and that
the network fetch is outside the DB transaction.

```mermaid
sequenceDiagram
    autonumber
    participant L as Poller loop
    participant W as WeatherClient
    participant O as Open-Meteo
    participant R as Repository
    participant D as Detection engine
    participant DB as PostgreSQL

    loop every POLL_INTERVAL_SECONDS, per city
        L->>W: fetch_current(city)
        W->>O: GET /forecast?current=...
        alt success
            O-->>W: 200 current{...}
            W-->>L: ReadingData (UTC)
        else transient error
            W->>O: retry (linear backoff)
            W-->>L: WeatherAPIError (after retries)
            L->>L: log WARNING, continue to next city
        end
        L->>R: store_reading(reading)
        alt new (city, timestamp)
            R->>DB: INSERT reading
            L->>R: get_history / peers / cooldown state
            R->>DB: SELECT recent
            L->>D: detect_events(reading, history, peers, cooldown)
            D-->>L: [events]
            L->>R: store_events(events)
            R->>DB: INSERT events
        else duplicate
            R-->>L: (existing, created=False)
            L->>L: skip detection (no events)
        end
    end
```

**Jump to source:** [app/poller.py](app/poller.py) · [app/weather_client.py](app/weather_client.py) · [app/detection/detector.py](app/detection/detector.py)

---

## 5. Sequence — an API request

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant API as FastAPI (api)
    participant R as Repository
    participant DB as PostgreSQL

    C->>API: GET /events?city=Ottawa&limit=50
    API->>API: validate query params (Pydantic)
    API->>R: get_recent_events(city, limit)
    R->>DB: SELECT ... ORDER BY detected_at DESC LIMIT n
    DB-->>R: rows
    R-->>API: ORM rows
    API->>API: serialize to EventOut (from_attributes)
    API-->>C: 200 {"events": [...]}
```

**Jump to source:** [app/main.py](app/main.py) · [app/schemas.py](app/schemas.py)

---

## 6. Detection decision flow

```mermaid
flowchart TD
    R[New reading] --> D{New city + timestamp?}
    D -- duplicate --> X[skip: no detection]
    D -- yes --> S[store reading]
    S --> H[load rolling history, peers, cooldown state]
    H --> A[anomaly: robust z vs baseline]
    H --> RC[rapid_change: delta floor + delta-z]
    H --> T[trend: monotonic cumulative]
    H --> P[precipitation: state machine]
    H --> C[condition_change: WMO tier]
    H --> W[high_wind: absolute tiers]
    H --> XC[cross_city: temp spread]
    A --> CD{Cooldown elapsed for\ncity + type + field?}
    RC --> CD
    T --> CD
    P --> CD
    C --> CD
    W --> CD
    XC --> CD
    CD -- no --> SUP[suppress]
    CD -- yes --> ST[(store event)]
```

**Jump to source:** [app/detection/rules.py](app/detection/rules.py) · [app/detection/detector.py](app/detection/detector.py) · [app/detection/baselines.py](app/detection/baselines.py)

---

## 7. Precipitation state machine

Precipitation is zero-inflated, so it is modelled as a state machine rather than
scored statistically.

```mermaid
stateDiagram-v2
    [*] --> Dry
    Dry --> Wet: precip > 0 mm/h\n→ precip_onset (tiered severity)
    Wet --> Dry: precip = 0 mm/h\n→ precip_cessation
    Wet --> Wet: still raining\n(no event)
    Dry --> Dry: still dry\n(no event)
```

**Jump to source:** [app/detection/rules.py](app/detection/rules.py) (`detect_precip_transition`)

---

## 8. Data model (ER)

```mermaid
erDiagram
    READING {
        int id PK
        string city "indexed; UNIQUE(city,timestamp)"
        datetime timestamp "UTC; upstream obs time"
        float temperature_2m
        float apparent_temperature
        float precipitation
        float wind_speed_10m
        int weather_code
        datetime created_at
    }
    EVENT {
        int id PK
        string city "indexed"
        string event_type "indexed"
        string field
        string severity "info|notable|severe"
        float observed_value
        float baseline_value
        float deviation "z / delta / spread"
        string reason "human-readable"
        json context "mad, window, tiers, peers..."
        datetime reading_timestamp "indexed; joins READING.timestamp"
        datetime detected_at "indexed"
    }
    READING ||..o{ EVENT : "describes (by city + timestamp)"
```

There is no hard FK between them (events are derived, and a reading may yield
0..n events); they relate logically on `(city, reading_timestamp)`.

**Jump to source:** [app/models.py](app/models.py)

---

## 9. Cloud deployment (AWS)

The container-per-role design maps cleanly onto managed services. Target: a
small, secure, observable footprint that the same image deploys into unchanged.

```mermaid
flowchart TB
    dev[GitHub] -->|Actions: build + test| ecr[(Amazon ECR\nimage registry)]

    subgraph aws[AWS VPC]
        alb[Application Load Balancer\nHTTPS via ACM]
        subgraph priv[Private subnets]
            apifg[ECS Fargate: api service\nautoscaled behind ALB]
            pollfg[ECS Fargate: poller service\n1 task, no inbound]
            rds[(RDS PostgreSQL\nMulti-AZ + backups)]
        end
        sm[Secrets Manager\nDATABASE_URL]
        cw[CloudWatch Logs + Alarms\nJSON logs ingested as-is]
    end
    users([Clients]) -->|HTTPS| alb --> apifg
    ecr -.image.-> apifg
    ecr -.image.-> pollfg
    apifg --> rds
    pollfg --> rds
    pollfg -->|GET| om[(Open-Meteo)]
    sm -.injected.-> apifg
    sm -.injected.-> pollfg
    apifg -.logs/metrics.-> cw
    pollfg -.logs/metrics.-> cw
```

**Service choices and why**

| Concern | Service | Why |
|---|---|---|
| Image registry | **Amazon ECR** | Private registry; GitHub Actions pushes the same image CI already builds. |
| Compute | **ECS on Fargate** — `api` + `poller` as two services | Serverless containers, no nodes to manage; the existing two-process split maps 1:1. The API service autoscales; the poller runs a single task. |
| Ingress | **ALB + ACM** | TLS termination, health checks against `/health`, path routing. |
| Database | **RDS for PostgreSQL** (Multi-AZ) | Managed Postgres with backups/failover; the app is already plain Postgres. Aurora Serverless v2 if bursty. |
| Secrets | **Secrets Manager / SSM** | `DATABASE_URL` injected into task definitions; nothing in the image or repo. |
| Logs/metrics | **CloudWatch** (+ optional Managed Grafana/Prometheus) | The service already emits one-line JSON, ingested and queryable as-is; alarms on poll success rate. |
| IaC | **Terraform** (or AWS Copilot) | Reproducible, reviewable infra. |
| CI/CD | **GitHub Actions → ECR → ECS deploy** | Extends the existing pipeline: on green `main`, build, push, update the ECS services. |

**Serverless variant.** Poller as a **Lambda on an EventBridge Scheduler** (hourly,
matching upstream cadence) instead of an always-on task; API as **Lambda + API
Gateway** via an ASGI adapter (Mangum); **Aurora Serverless v2** scaling to near-zero
when idle — cheapest for low, spiky traffic.

**GCP equivalent.** Cloud Run (api) · Cloud Run Job + Cloud Scheduler (poller) ·
Cloud SQL for PostgreSQL · Secret Manager · Cloud Logging/Monitoring · Artifact
Registry.

---

## 10. How I would scale it

Today: 3 cities, hourly data, one poller. The path from here to thousands of
stations and high read traffic:

```mermaid
flowchart LR
    subgraph ingest[Ingestion at scale]
        sch[EventBridge Scheduler] -->|enqueue stations| q[[SQS queue]]
        q --> p1[poller worker]
        q --> p2[poller worker]
        q --> p3[poller worker]
    end
    p1 & p2 & p3 --> tsdb[(TimescaleDB\nhypertables + continuous aggregates)]
    tsdb --> ro[(read replicas)]
    subgraph serve[Serving at scale]
        alb[ALB] --> api1[api] & api2[api]
        api1 & api2 --> cache[(Redis cache)]
        api1 & api2 --> ro
    end
```

- **Poller throughput.** Replace the single loop with a **work queue (SQS)**: a
  scheduler enqueues "stations due to poll", and a pool of stateless workers
  drains it. The `UNIQUE(city, timestamp)` dedup makes re-delivery safe, so
  workers need no coordination beyond the queue. Shard by region if needed.
- **Database.** Move readings to **TimescaleDB hypertables** (time partitioning),
  use **continuous aggregates** to precompute rolling baselines instead of
  recomputing per poll, add **read replicas** for the API, and apply
  **retention/downsampling** policies for old raw data.
- **Detection.** For many stations, move per-reading Python detection into a
  **stream processor** (Kafka + Flink/Faust) maintaining incremental per-station
  baselines, or refresh baselines from materialized views — the detectors stay
  pure, only their inputs change.
- **API.** Already stateless → autoscale horizontally; add a **Redis cache** for
  hot queries (`/health` counts, latest readings/events) with write-through
  invalidation.
- **Resilience.** Circuit breaker + rate limiting around the upstream API, a
  **dead-letter queue** for repeatedly failing polls, and per-source backoff
  (the client already retries with backoff).
- **Observability.** Prometheus metrics + OpenTelemetry traces, Grafana
  dashboards, and alerts on *poll success rate* and *abnormal event volume*
  (a monitor that goes silent is itself an incident).
- **Delivery.** Blue/green ECS deploys; multi-region active/passive for HA.
