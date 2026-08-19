.PHONY: install test run docker-build docker-up clean

install:
	python3 -m venv venv
	./venv/bin/pip install -r requirements.txt

test:
	./venv/bin/pytest -v tests/

run:
	./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf __pycache__ .pytest_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
