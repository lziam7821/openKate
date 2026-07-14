PYTHON := .venv/bin/python
PIP := .venv/bin/pip
PYTHONPATH := packages/python:services/project-service:services/validation-service:services/report-service:services/execution-service:services/workflow-service:services/gateway-service

.PHONY: bootstrap test lint build up down project validation report execution workflow gateway executor-ui executor-api executor-state health

bootstrap:
	python3 -m venv .venv
	$(PIP) install -r requirements-dev.txt
	pnpm install

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest -q
	pnpm --filter @openkate/web test
	pnpm --filter @openkate/web build

lint:
	$(PYTHON) -m ruff check packages/python services workers tests
	$(PYTHON) -m mypy packages/python
	pnpm --filter @openkate/web lint

build:
	docker compose --profile core --profile ai --profile executors --profile observability build

up:
	docker compose --profile core --profile ai --profile executors --profile observability up --build

down:
	docker compose --profile core --profile ai --profile executors --profile observability down

project:
	PYTHONPATH=packages/python:services/project-service $(PYTHON) -m uvicorn app.main:app --app-dir services/project-service --port 8001 --reload

gateway:
	PYTHONPATH=packages/python:services/gateway-service $(PYTHON) -m uvicorn app.main:app --app-dir services/gateway-service --port 8000 --reload

validation:
	PYTHONPATH=packages/python:services/validation-service $(PYTHON) -m uvicorn app.main:app --app-dir services/validation-service --port 8002 --reload

report:
	PYTHONPATH=packages/python:services/report-service $(PYTHON) -m uvicorn app.main:app --app-dir services/report-service --port 8003 --reload

execution:
	PYTHONPATH=packages/python:services/execution-service $(PYTHON) -m uvicorn app.main:app --app-dir services/execution-service --port 8004 --reload

workflow:
	PYTHONPATH=packages/python:services/workflow-service $(PYTHON) -m uvicorn app.main:app --app-dir services/workflow-service --port 8005 --reload

executor-ui:
	PYTHONPATH=packages/python:workers/executor-ui $(PYTHON) -m uvicorn app.main:app --app-dir workers/executor-ui --port 8011 --reload

executor-api:
	PYTHONPATH=packages/python:workers/executor-api $(PYTHON) -m uvicorn app.main:app --app-dir workers/executor-api --port 8012 --reload

executor-state:
	PYTHONPATH=packages/python:workers/executor-state $(PYTHON) -m uvicorn app.main:app --app-dir workers/executor-state --port 8013 --reload

health:
	curl -fsS http://127.0.0.1:8000/api/v1/system/health
