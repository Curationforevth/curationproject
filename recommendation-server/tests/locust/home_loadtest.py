"""Phase 1B /home 부하 테스트.

사용:
  cd recommendation-server
  pip install locust
  API=https://curation-recommendation.onrender.com JWT=... UID=... \\
    locust -f tests/locust/home_loadtest.py --host=$API \\
      --users=10 --spawn-rate=2 --run-time=60s --headless
"""
import os
from locust import HttpUser, task, between


class HomeUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.jwt = os.environ["JWT"]
        self.uid = os.environ["UID"]

    @task
    def get_home(self):
        self.client.get(
            f"/home/{self.uid}",
            headers={"Authorization": f"Bearer {self.jwt}"},
            name="/home/{uid}",
        )
