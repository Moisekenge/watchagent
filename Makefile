# Developer convenience targets. Thin wrappers around docker compose + pytest.
.PHONY: up down logs ps test lint seed clean

up:            ## Build and start the full stack (db + poller + api)
	docker compose up --build -d

down:          ## Stop and remove containers (keeps the database volume)
	docker compose down

logs:          ## Follow logs from all services
	docker compose logs -f

ps:            ## Show service status
	docker compose ps

test:          ## Run the unit test suite
	pytest -q

lint:          ## Lint the codebase
	ruff check app tests scripts

seed:          ## Populate the running stack with the reproducible sample dataset
	docker compose exec api python scripts/generate_demo_data.py --reset

clean:         ## Stop everything and DELETE the database volume
	docker compose down -v
