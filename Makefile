VENV=backend/.venv
.PHONY: install dev
install:
	python3 -m venv $(VENV) && $(VENV)/bin/pip install -e "backend[dev]"
dev:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
