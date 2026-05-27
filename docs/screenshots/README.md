# Screenshots

Proof-of-running images referenced by the top-level [README](../../README.md#proof-it-runs).
Drop the three PNGs below into this folder and they render automatically.

| File | What to capture |
|------|-----------------|
| `docker-desktop.png` | Docker Desktop (or `docker compose ps`) showing the three **watchagent** containers — `watchagent-api-1`, `watchagent-db-1` (healthy), `watchagent-poller-1` — running. |
| `swagger-docs.png` | The interactive Swagger UI at <http://localhost:8000/docs> with `/health`, `/readings`, `/events` expanded. |
| `events-response.png` | A real `/events` response — either the browser at `http://localhost:8000/events?limit=5` or a `curl` in the terminal — showing populated event records with their `reason` fields. |

## How to produce them

```bash
cp .env.example .env
docker compose up --build -d
# populate a rich event set so the screenshots are interesting:
docker compose exec api python scripts/generate_demo_data.py --reset
```

Then open <http://localhost:8000/docs> and <http://localhost:8000/events?limit=5>,
and take the three screenshots. Keep them reasonably sized (≈1–2 MB each).
