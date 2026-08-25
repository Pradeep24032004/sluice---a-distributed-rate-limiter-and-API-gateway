.PHONY: up down logs test load-test benchmark

up:
	docker compose up -d --build

down:
	docker compose down -v

logs:
	docker compose logs -f gateway1 gateway2 gateway3

test:
	pytest -v

load-test:
	locust -f load_test/locustfile.py --host http://localhost:8080

benchmark:
	PYTHONPATH=. python scripts/benchmark_scaling.py
