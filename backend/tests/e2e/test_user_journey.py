import os
import time
from uuid import uuid4

import httpx
import pytest


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("RUN_E2E") != "1",
        reason="set RUN_E2E=1 and start the complete Docker Compose stack",
    ),
]

GATEWAY_BASE_URL = os.getenv("GATEWAY_BASE_URL", "http://localhost:8081")


def test_search_event_reaches_stream_worker_and_trending_endpoint() -> None:
    session_id = str(uuid4())
    event_id = str(uuid4())
    unique_query = f"e2e-{event_id[:8]}"

    # Vinext validates the public Host header. Inside the Compose network the
    # URL is `gateway`, so emulate the browser-facing gateway host explicitly.
    with httpx.Client(
        base_url=GATEWAY_BASE_URL,
        timeout=15,
        headers={"Host": "localhost:58081"},
    ) as client:
        frontend = client.get("/")
        search = client.post(
            "/api/v1/search",
            headers={"X-Session-ID": session_id},
            json={
                "query": "cà phê gần Bến Thành",
                "latitude": 10.7757,
                "longitude": 106.7009,
                "radius": 5000,
                "limit": 5,
            },
        )
        ingestion = client.post(
            "/api/v1/events/batch",
            headers={"X-Session-ID": session_id},
            json={
                "events": [
                    {
                        "id": event_id,
                        "event_type": "search",
                        "session_id": session_id,
                        "query": unique_query,
                    }
                ]
            },
        )

        assert frontend.status_code == 200
        assert search.status_code == 200 and search.json()["results"]
        assert ingestion.status_code == 202
        assert ingestion.json()["accepted"] == 1

        deadline = time.monotonic() + 15
        status = None
        while time.monotonic() < deadline:
            status = client.get(
                "/api/v1/ingestion/status",
                params={"session_id": session_id},
            ).json()
            if status["processed"] == 1:
                break
            time.sleep(0.25)

        assert status is not None and status["processed"] == 1
        trending = client.get("/api/v1/trending", params={"limit": 50}).json()
        assert any(item["query"] == unique_query for item in trending["queries"])
