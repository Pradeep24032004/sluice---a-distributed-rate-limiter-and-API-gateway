"""Load test for the gateway, run through the nginx LB so requests spread
across all gateway instances (proves rate limits are shared via Redis,
not per-instance).

Usage:
    locust -f load_test/locustfile.py --host http://localhost:8080

Two user classes:
- SteadyUser: stays under the configured limit -> should see ~0% 429s.
- BurstyUser: fires far above the limit -> exercises the 429 path and
  lets you measure throughput/latency under throttling.
"""

import random

from locust import HttpUser, between, task


class SteadyUser(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self):
        self.api_key = f"steady-{random.randint(1, 500)}"

    @task
    def call_gateway(self):
        self.client.get(
            "/proxy/orders/123",
            headers={"X-API-Key": self.api_key},
        )


class BurstyUser(HttpUser):
    wait_time = between(0, 0.05)

    def on_start(self):
        self.api_key = f"bursty-{random.randint(1, 5)}"

    @task
    def call_gateway(self):
        self.client.get(
            "/proxy/orders/123",
            headers={"X-API-Key": self.api_key},
        )
