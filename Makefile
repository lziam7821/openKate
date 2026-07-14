PYTHON := .venv/bin/python
PIP := .venv/bin/pip
PYTHONPATH := packages/python:services/project-service:services/gateway-service

.PHONY: bootstrap test project gateway health

bootstrap:
	python3 -m venv .venv
	$(PIP) install -r requirements-dev.txt

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest -q

project:
	PYTHONPATH=packages/python:services/project-service $(PYTHON) -m uvicorn app.main:app --app-dir services/project-service --port 8001 --reload

gateway:
	PYTHONPATH=packages/python:services/gateway-service $(PYTHON) -m uvicorn app.main:app --app-dir services/gateway-service --port 8000 --reload

health:
	curl -fsS http://127.0.0.1:8000/api/v1/system/health

