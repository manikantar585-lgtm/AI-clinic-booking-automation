.PHONY: install dev lint format typecheck test eval check run docker

install:            ## runtime deps only
	pip install -r requirements.txt

dev:                ## dev deps + git hooks
	pip install -r requirements-dev.txt
	pre-commit install

lint:
	ruff check app tests evals
	ruff format --check app tests evals

format:
	ruff check --fix app tests evals
	ruff format app tests evals

typecheck:
	mypy

test:
	pytest --cov

eval:               ## NLU accuracy of the rule parser (add EXTRACTOR=llm to test the LLM)
	python -m evals.run --extractor $(or $(EXTRACTOR),rules) --show-failures

check: lint typecheck test eval   ## everything CI runs

run:
	uvicorn app.main:app --reload

docker:
	docker compose up --build
