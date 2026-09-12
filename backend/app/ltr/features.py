"""Vector đặc trưng cho Learning-to-Rank — nguồn sự thật duy nhất.

Module này cố ý KHÔNG phụ thuộc gì ngoài thư viện chuẩn: cả script huấn luyện
(chạy ngoài container) lẫn đường phục vụ (`ranking.rerank`) đều import nó, nên
nó phải nhẹ và không kéo theo psycopg/redis.

## Vì sao chỉ một chỗ định nghĩa

Lỗi kinh điển của LTR là *train/serve skew*: lúc huấn luyện tính đặc trưng bằng
một đoạn code, lúc phục vụ tính bằng đoạn khác, rồi hai bên lệch nhau vài phần
trăm và mô hình mất hết ý nghĩa mà không có test nào bắt được. Ở đây chỉ có
`extract_features()`; cả hai phía gọi đúng hàm đó trên cùng một dict candidate.

## Vì sao NaN chứ không phải 0

LightGBM xử lý giá trị thiếu ngay trong thuật toán tách nhánh: nó thử cho mẫu
thiếu đi nhánh trái rồi nhánh phải và chọn bên nào giảm loss nhiều hơn. Nghĩa
là "chưa có ai đánh giá" được học như một trạng thái riêng, thay vì bị nhét
thành "bị chấm 0/5" — đúng cái lỗi mà migration 0008 đi sửa.

Quy ước xuyên suốt: `float("nan")` = không có dữ liệu; 0.0 = có dữ liệu và
bằng không.

## Những gì CỐ Ý không phải đặc trưng

- ``rank`` / vị trí hiển thị — đây là kết quả của bộ xếp hạng cũ. Đưa vào thì
  mô hình học "cái gì đang xếp cao thì đúng", tức là học thuộc chính nó.
- ``source`` (seed / openstreetmap) — 28 POI seed có rating, mô tả và
  popularity gõ tay, còn 2982 POI thật thì không. Cho mô hình biết cột này thì
  nó chỉ cần học "seed = tốt" là đạt điểm cao trên tập đánh giá, trong khi
  không học được gì về mức liên quan.
- ``sponsored`` — vị trí tài trợ là luật nghiệp vụ áp sau khi xếp hạng, không
  phải bằng chứng về mức liên quan.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

NAN = float("nan")

# Thứ tự trong danh sách này LÀ thứ tự cột của ma trận đặc trưng. Chỉ được
# thêm vào CUỐI; đổi thứ tự làm mọi mô hình đã lưu trở nên vô nghĩa mà không
# báo lỗi. `model.py` đối chiếu danh sách này với danh sách lưu kèm mô hình.
FEATURE_NAMES: tuple[str, ...] = (
    # --- Khớp văn bản -----------------------------------------------------
    "text_score",
    "bm25_score",
    "fusion_score_norm",
    "n_channels",
    # --- Không gian -------------------------------------------------------
    "distance_meters",
    "spatial_decay",
    "log_distance",
    # --- Thời gian / ngữ cảnh ---------------------------------------------
    "is_open",
    "closes_in_minutes",
    "opens_in_minutes",
    "hour_of_day",
    "is_weekend",
    "category_time_match",
    "context_score",
    # --- Chất lượng địa điểm ----------------------------------------------
    "rating",
    "has_rating",
    "review_count",
    "log_review_count",
    "popularity_score",
    "price_level",
    # --- Hành vi thời gian thực -------------------------------------------
    "trending_score",
    "recency_score",
    "pop_w15",
    "pop_w1h",
    "pop_w24h",
    # --- Tín hiệu cá nhân hóa / đồ thị ------------------------------------
    "region_ctr",
    "in_graph",
    "category_affinity",
    # --- Thêm sau, PHẢI ở cuối (xem quy ước ngay trên) ---------------------
    "vector_score",
)

SPATIAL_DECAY_METERS = 1_500.0

_BUCKET_ORDER = {"morning": 0, "noon": 1, "afternoon": 2, "evening": 3, "night": 4}


def _num(value: Any) -> float:
    """Ép về float, trả NaN cho None và mọi thứ không ép được."""
    if value is None:
        return NAN
    try:
        result = float(value)
    except (TypeError, ValueError):
        return NAN
    return result if math.isfinite(result) else NAN


def _flag(value: Any) -> float:
    """Cờ ba trạng thái: 1.0 đúng, 0.0 sai, NaN chưa biết."""
    if value is None:
        return NAN
    return 1.0 if value else 0.0


def extract_features(
    candidate: Mapping[str, Any],
    category_boost: Mapping[str, float] | None = None,
    graph_boost: set[str] | frozenset[str] | None = None,
) -> list[float]:
    """Đổi một candidate đã làm giàu thành vector số theo ``FEATURE_NAMES``.

    ``candidate`` là dict đi ra từ ``spatio_temporal.enrich_candidates`` và
    ``features.serving.attach_region_ctr`` — chính dict mà ``ranking.rerank``
    nhận, không phải bản rút gọn nào khác.
    """
    category_boost = category_boost or {}
    graph_boost = graph_boost or frozenset()

    distance = _num(candidate.get("distanceMeters"))
    spatial_decay = (
        math.exp(-distance / SPATIAL_DECAY_METERS) if math.isfinite(distance) else NAN
    )
    log_distance = math.log1p(distance) if math.isfinite(distance) else NAN

    time_context = candidate.get("timeContext") or {}
    windows = candidate.get("popularityWindows") or {}

    # rating/review_count CHỈ được coi là dữ liệu thật khi ratingSource='user'
    # (đánh giá thật của người dùng ứng dụng này, xem app/reviews.py). 'seed'
    # (28 POI gõ tay) và 'google' (bên thứ ba) bị coi là THIẾU dữ liệu ở đây —
    # cùng lý do cột `source` đã bị loại khỏi feature set (xem docstring đầu
    # file): cho model thấy rating gõ tay/mua ngoài thì nó học "seed = tốt"
    # thay vì học mức liên quan thật. Hiển thị công khai (`/api/v1/pois/{id}`)
    # vẫn dùng đúng thứ tự ưu tiên user > google > seed như trước — chỉ riêng
    # feature vector LTR này chặt chẽ hơn.
    rating_is_real = candidate.get("ratingSource") == "user"
    rating = _num(candidate.get("rating")) if rating_is_real else NAN
    # rating = 0 vẫn có thể xuất hiện ở dữ liệu cũ chưa chạy migration 0008;
    # coi nó là thiếu dữ liệu để hai bên train/serve nhất quán.
    if rating == 0.0:
        rating = NAN

    review_count = _num(candidate.get("reviewCount")) if rating_is_real else NAN
    price_level = _num(candidate.get("priceLevel"))
    # priceLevel 0 nghĩa là CHƯA RÕ chứ không phải "rẻ nhất" — cùng lập luận
    # với `ranking.ensure_price_diversity`.
    if price_level == 0.0:
        price_level = NAN

    values: dict[str, float] = {
        "text_score": _num(candidate.get("textScore")),
        "bm25_score": _num(candidate.get("bm25Score")),
        "fusion_score_norm": _num(candidate.get("fusionScoreNorm")),
        "n_channels": float(len(candidate.get("retrievalChannels") or [])),
        "distance_meters": distance,
        "spatial_decay": spatial_decay,
        "log_distance": log_distance,
        "is_open": _flag(candidate.get("openNow")),
        "closes_in_minutes": _num(candidate.get("closesInMinutes")),
        "opens_in_minutes": _num(candidate.get("opensInMinutes")),
        "hour_of_day": _num(time_context.get("hour")),
        "is_weekend": _flag(time_context.get("isWeekend")),
        "category_time_match": _num(time_context.get("categoryMatchesTime")),
        "context_score": _num(candidate.get("contextScore")),
        "rating": rating,
        "has_rating": 0.0 if math.isnan(rating) else 1.0,
        "review_count": review_count,
        "log_review_count": (
            math.log1p(review_count) if math.isfinite(review_count) else NAN
        ),
        "popularity_score": _num(candidate.get("popularityScore")),
        "price_level": price_level,
        "trending_score": _num(candidate.get("trendingScore")),
        "recency_score": _num(candidate.get("recencyScore")),
        "pop_w15": _num(windows.get("w15")),
        "pop_w1h": _num(windows.get("w1h")),
        "pop_w24h": _num(windows.get("w24h")),
        "region_ctr": _num(candidate.get("regionCtr")),
        "in_graph": 1.0 if candidate.get("id") in graph_boost else 0.0,
        "category_affinity": float(category_boost.get(candidate.get("category"), 0.0)),
        "vector_score": _num(candidate.get("vectorScore")),
    }
    return [values[name] for name in FEATURE_NAMES]


def feature_row(candidate: Mapping[str, Any], **kwargs: Any) -> dict[str, float]:
    """Như ``extract_features`` nhưng trả dict tên -> giá trị, để ghi JSONL và
    để đọc khi gỡ lỗi."""
    return dict(zip(FEATURE_NAMES, extract_features(candidate, **kwargs)))


def coverage(rows: list[list[float]]) -> dict[str, float]:
    """Tỉ lệ giá trị KHÔNG thiếu của từng đặc trưng trên một tập dòng.

    Dùng để báo cáo trung thực: một đặc trưng phủ 1% thì mô hình gần như không
    học được gì từ nó, và con số đó phải xuất hiện trong báo cáo chứ không nằm
    im trong ma trận.
    """
    if not rows:
        return {name: 0.0 for name in FEATURE_NAMES}
    total = len(rows)
    result: dict[str, float] = {}
    for index, name in enumerate(FEATURE_NAMES):
        present = sum(1 for row in rows if not math.isnan(row[index]))
        result[name] = round(present / total, 4)
    return result
