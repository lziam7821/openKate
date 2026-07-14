PYTHON := .venv/bin/python
PIP := .venv/bin/pip
PYTHONPATH := packages/python:services/project-service:services/validation-service:services/report-service:services/gateway-service

.PHONY: bootstrap test project validation report gateway health

bootstrap:
	python3 -m venv .venv
	$(PIP) install -r requirements-dev.txt

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest -q

project:
	PYTHONPATH=packages/python:services/project-service $(PYTHON) -m uvicorn app.main:app --app-dir services/project-service --port 8001 --reload

gateway:
	PYTHONPATH=packages/python:services/gateway-service $(PYTHON) -m uvicorn app.main:app --app-dir services/gateway-service --port 8000 --reload

validation:
	PYTHONPATH=packages/python:services/validation-service $(PYTHON) -m uvicorn app.main:app --app-dir services/validation-service --port 8002 --reload

report:
	PYTHONPATH=packages/python:services/report-service $(PYTHON) -m uvicorn app.main:app --app-dir services/report-service --port 8003 --reload

health:
	curl -fsS http://127.0.0.1:8000/api/v1/system/health
