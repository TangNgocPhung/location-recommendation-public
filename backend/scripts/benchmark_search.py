"""Đo độ trễ endpoint tìm kiếm và LƯU kết quả ra file.

Chạy:
    API_BASE_URL=http://localhost:8000 BENCHMARK_CONCURRENCY=10 python scripts/benchmark_search.py

Kết quả ghi vào `backend/results/latency_<timestamp>.json`.

`BENCHMARK_CONCURRENCY` (mặc định 1) chạy nhiều luồng song song. Đo ở các mức
1 / 10 / 50 luồng cho thấy hệ thống xuống cấp thế nào khi tải tăng — một con số
p95 đo ở một luồng duy nhất không nói được điều gì về khả năng chịu tải.
"""

import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).parents[1]
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
BENCHMARK_REQUESTS = int(os.getenv("BENCHMARK_REQUESTS", "50"))
BENCHMARK_CONCURRENCY = max(1, int(os.getenv("BENCHMARK_CONCURRENCY", "1")))
MAX_SEARCH_P95_MS = float(os.getenv("MAX_SEARCH_P95_MS", "500"))
MAX_ERROR_RATE = float(os.getenv("MAX_ERROR_RATE", "0.01"))
RESULTS_DIR = PROJECT_ROOT / "results"

PAYLOAD = {
    "query": "cà phê gần Bến Thành",
    "latitude": 10.7757,
    "longitude": 106.7009,
    "radius": 5000,
    "limit": 20,
}


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * quantile + 0.9999) - 1))
    return ordered[index]


def _one_request(client: httpx.Client) -> tuple[float, bool]:
    started = time.perf_counter()
    failed = False
    try:
        response = client.post("/api/v1/search", json=PAYLOAD)
        failed = response.status_code >= 400
    except httpx.HTTPError:
        failed = True
    return (time.perf_counter() - started) * 1000, failed


def main() -> None:
    durations: list[float] = []
    errors = 0

    limits = httpx.Limits(
        max_connections=BENCHMARK_CONCURRENCY, max_keepalive_connections=BENCHMARK_CONCURRENCY
    )
    with httpx.Client(base_url=API_BASE_URL, timeout=15, limits=limits) as client:
        # Một lần gọi làm nóng, không tính vào số liệu: lần đầu phải trả giá cho
        # kết nối, cache và JIT của tầng dưới.
        client.post("/api/v1/search", json=PAYLOAD).raise_for_status()
        started_wall = time.perf_counter()
        if BENCHMARK_CONCURRENCY == 1:
            outcomes = [_one_request(client) for _ in range(BENCHMARK_REQUESTS)]
        else:
            with ThreadPoolExecutor(max_workers=BENCHMARK_CONCURRENCY) as pool:
                outcomes = list(
                    pool.map(lambda _: _one_request(client), range(BENCHMARK_REQUESTS))
                )
        wall_seconds = time.perf_counter() - started_wall

    for duration, failed in outcomes:
        durations.append(duration)
        errors += 1 if failed else 0

    error_rate = errors / BENCHMARK_REQUESTS
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = {
        "measuredAt": stamp,
        "apiBaseUrl": API_BASE_URL,
        "requests": BENCHMARK_REQUESTS,
        "concurrency": BENCHMARK_CONCURRENCY,
        "p50_ms": round(statistics.median(durations), 2),
        "p95_ms": round(percentile(durations, 0.95), 2),
        "p99_ms": round(percentile(durations, 0.99), 2),
        "max_ms": round(max(durations), 2),
        "throughput_rps": round(BENCHMARK_REQUESTS / wall_seconds, 2) if wall_seconds else 0.0,
        "error_rate": round(error_rate, 4),
        "thresholds": {"max_p95_ms": MAX_SEARCH_P95_MS, "max_error_rate": MAX_ERROR_RATE},
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"latency_{stamp}_c{BENCHMARK_CONCURRENCY}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"đã ghi {out.relative_to(PROJECT_ROOT)}")

    if result["p95_ms"] > MAX_SEARCH_P95_MS or error_rate > MAX_ERROR_RATE:
        print(
            f"FAIL: p95={result['p95_ms']}ms (ngưỡng {MAX_SEARCH_P95_MS}) "
            f"error_rate={error_rate} (ngưỡng {MAX_ERROR_RATE})",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
