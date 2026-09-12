"""Dựng khung file ground truth để gán nhãn tay.

Chạy:
    API_BASE_URL=http://localhost:8000 python scripts/build_judgment_template.py

Script gọi API thật cho từng truy vấn, ghi ra `tests/fixtures/judgment_template.json`
gồm danh sách POI ứng viên kèm tên/địa chỉ/khoảng cách và một ô `grade` để trống.

Script KHÔNG tự gán nhãn. Tự chấm rồi tự đo là vòng luẩn quẩn: hệ thống sẽ luôn
đạt điểm cao vì thước đo được sinh ra từ chính đầu ra của nó. Việc của con người
là điền `grade` theo tiêu chí trong `docs/quality-gates.md`, LÝ TƯỞNG NHẤT là
gán nhãn dựa trên tên và địa chỉ mà KHÔNG nhìn thứ tự hệ thống trả về (cột
`serverRank` để sẵn chỉ để đối chiếu SAU khi đã chấm xong).

Sau khi điền xong, đổi tên/gộp vào `relevance_judgments.json`; script đo chỉ đọc
các cặp có `grade >= 1`.
"""

import json
import os
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).parents[1]
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
OUT_PATH = PROJECT_ROOT / "tests" / "fixtures" / "judgment_template.json"
TOP_N = int(os.getenv("TEMPLATE_TOP_N", "10"))

# Ba tâm truy vấn khác nhau để kiểm tra spatial decay, không dồn hết về Quận 1.
BEN_THANH = (10.7757, 106.7009)   # Quận 1, trung tâm
THU_DUC = (10.8494, 106.7537)     # TP Thủ Đức, ngoại vi
QUAN_6 = (10.7460, 106.6350)      # Quận 6, phía tây

# 40 truy vấn phủ sáu nhóm. Mỗi nhóm kiểm tra một cơ chế khác nhau của hệ thống;
# nếu chỉ đo bằng truy vấn "đẹp" thì không phát hiện được điểm yếu ở đâu.
QUERIES: list[tuple[str, str, tuple[float, float], int]] = [
    # (truy vấn, nhóm, tâm, bán kính) — nhóm "ngắn": BM25 cơ bản, một token
    ("phở", "ngắn", BEN_THANH, 5000),
    ("bún", "ngắn", BEN_THANH, 5000),
    ("cơm", "ngắn", BEN_THANH, 5000),
    ("cà phê", "ngắn", BEN_THANH, 5000),
    ("bánh mì", "ngắn", BEN_THANH, 5000),
    ("trà sữa", "ngắn", BEN_THANH, 5000),
    ("công viên", "ngắn", BEN_THANH, 5000),
    ("bảo tàng", "ngắn", BEN_THANH, 5000),
    # nhóm "địa danh": geo-parser phải tách được địa danh khỏi chủ ngữ
    ("cà phê gần Bến Thành", "địa danh", BEN_THANH, 5000),
    ("quán ăn gần chợ Bến Thành", "địa danh", BEN_THANH, 5000),
    ("phở ở Quận 1", "địa danh", BEN_THANH, 5000),
    ("cà phê Thủ Đức", "địa danh", THU_DUC, 5000),
    ("nhà hàng gần Nhà thờ Đức Bà", "địa danh", BEN_THANH, 5000),
    ("quán cà phê gần Dinh Độc Lập", "địa danh", BEN_THANH, 5000),
    ("ăn uống Quận 6", "địa danh", QUAN_6, 5000),
    # nhóm "không dấu": analyzer bỏ dấu phải khớp với văn bản có dấu
    ("ca phe", "không dấu", BEN_THANH, 5000),
    ("pho ngon", "không dấu", BEN_THANH, 5000),
    ("banh mi", "không dấu", BEN_THANH, 5000),
    ("quan an", "không dấu", BEN_THANH, 5000),
    ("cong vien", "không dấu", BEN_THANH, 5000),
    ("tra sua", "không dấu", BEN_THANH, 5000),
    ("bao tang", "không dấu", BEN_THANH, 5000),
    # nhóm "sai chính tả": fuzzy BM25
    ("caphe", "sai chính tả", BEN_THANH, 5000),
    ("nha hnag", "sai chính tả", BEN_THANH, 5000),
    ("quan cà fê", "sai chính tả", BEN_THANH, 5000),
    ("banh my", "sai chính tả", BEN_THANH, 5000),
    ("cong vien tao dan", "sai chính tả", BEN_THANH, 5000),
    ("pho bo", "sai chính tả", BEN_THANH, 5000),
    # nhóm "theo giờ": contextScore của Enricher (giờ mở cửa, hợp thời điểm)
    ("ăn khuya", "theo giờ", BEN_THANH, 5000),
    ("cà phê sáng sớm", "theo giờ", BEN_THANH, 5000),
    ("quán bar buổi tối", "theo giờ", BEN_THANH, 5000),
    ("ăn sáng", "theo giờ", BEN_THANH, 5000),
    ("quán mở cửa bây giờ", "theo giờ", BEN_THANH, 5000),
    # nhóm "đa tâm": spatial decay ở các tâm khác nhau và bán kính khác nhau
    ("cà phê", "đa tâm", THU_DUC, 3000),
    ("quán ăn", "đa tâm", THU_DUC, 3000),
    ("cà phê", "đa tâm", QUAN_6, 3000),
    ("quán ăn", "đa tâm", QUAN_6, 3000),
    ("công viên", "đa tâm", THU_DUC, 10000),
    ("chợ", "đa tâm", QUAN_6, 3000),
    ("siêu thị", "đa tâm", THU_DUC, 5000),
]


def main() -> None:
    cases = []
    backends: set[str] = set()
    with httpx.Client(base_url=API_BASE_URL, timeout=20) as client:
        for query, group, (latitude, longitude), radius in QUERIES:
            response = client.post(
                "/api/v1/search",
                json={
                    "query": query,
                    "latitude": latitude,
                    "longitude": longitude,
                    "radius": radius,
                    "limit": TOP_N,
                },
            )
            response.raise_for_status()
            payload = response.json()
            backends.add(payload.get("retrievalBackend", "unknown"))
            candidates = [
                {
                    "poi_id": poi["id"],
                    "name": poi.get("name"),
                    "category": poi.get("categoryLabel") or poi.get("category"),
                    "address": poi.get("address"),
                    "distance_m": round(poi.get("distanceMeters") or 0),
                    "openNow": poi.get("openNow"),
                    "serverRank": poi.get("rank"),
                    "grade": None,  # <-- ĐIỀN 0..3 theo docs/quality-gates.md
                }
                for poi in payload["results"]
            ]
            cases.append(
                {
                    "query": query,
                    "group": group,
                    "latitude": latitude,
                    "longitude": longitude,
                    "radius": radius,
                    "candidates": candidates,
                }
            )
            print(f"{group:14} {query!r:32} -> {len(candidates)} ứng viên")

    OUT_PATH.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    labelled = sum(len(c["candidates"]) for c in cases)
    print(f"\n{len(cases)} truy vấn · {labelled} cặp cần chấm · backend={sorted(backends)}")
    print(f"đã ghi {OUT_PATH.relative_to(PROJECT_ROOT)}")
    if backends and backends != {"opensearch"}:
        print(
            f"CẢNH BÁO: đang lấy ứng viên từ đường dự phòng ({sorted(backends)}); "
            "dựng chỉ mục OpenSearch trước khi gán nhãn.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
