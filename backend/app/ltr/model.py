"""Phase 6 — nạp mô hình LTR và chấm điểm lúc phục vụ.

Ba nguyên tắc, theo thứ tự quan trọng:

1. **Không có mô hình thì không được hỏng.** ``score()`` trả ``None`` và tầng
   gọi dùng công thức tuyến tính như cũ. Xóa ``model.txt`` đi thì hệ thống
   phải chạy y hệt trước khi có Phase 5.
2. **Lệch đặc trưng thì từ chối nạp, không im lặng đoán.** Mô hình lưu kèm
   danh sách tên đặc trưng. Nếu ``features.FEATURE_NAMES`` đã đổi (thêm cột,
   đổi thứ tự) thì cột thứ i lúc phục vụ không còn là cột thứ i lúc huấn
   luyện, và mô hình sẽ cho điểm rác một cách hoàn toàn im lặng. Thà tắt LTR.
3. **Nạp một lần.** Đọc file mỗi request sẽ thêm hàng chục ms vào độ trễ —
   chính con số mà Phase 7 đem đi so sánh.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Mapping, Sequence

from .features import FEATURE_NAMES, extract_features

logger = logging.getLogger("nearby-ltr")

DEFAULT_MODEL_DIR = Path(__file__).resolve().parent
_lock = threading.Lock()
_cache: dict[str, Any] = {"loaded": False, "booster": None, "meta": None, "path": None}


def model_dir() -> Path:
    """Thư mục chứa ``model.txt``; đổi được qua biến môi trường ``LTR_MODEL_DIR``."""
    override = os.getenv("LTR_MODEL_DIR")
    return Path(override) if override else DEFAULT_MODEL_DIR


def load(force: bool = False) -> Any | None:
    """Nạp booster đã lưu, hoặc ``None`` nếu không có / không dùng được."""
    with _lock:
        if _cache["loaded"] and not force:
            return _cache["booster"]
        _cache.update({"loaded": True, "booster": None, "meta": None, "path": None})

        directory = model_dir()
        model_path = directory / "model.txt"
        meta_path = directory / "model.meta.json"
        if not model_path.exists():
            logger.info("Không có mô hình LTR ở %s — dùng xếp hạng tuyến tính", model_path)
            return None

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Không đọc được %s (%s) — tắt LTR", meta_path, error)
            return None

        saved_names = meta.get("featureNames")
        if saved_names is not None and list(saved_names) != list(FEATURE_NAMES):
            logger.error(
                "Mô hình LTR huấn luyện với %d đặc trưng khác với FEATURE_NAMES hiện "
                "tại (%d). Cột không còn khớp nên điểm số sẽ sai — tắt LTR, hãy "
                "huấn luyện lại.",
                len(saved_names),
                len(FEATURE_NAMES),
            )
            return None

        try:
            import lightgbm as lgb

            booster = lgb.Booster(model_file=str(model_path))
        except Exception as error:  # noqa: BLE001 - thiếu lightgbm cũng phải rơi về tuyến tính
            logger.warning("Không nạp được mô hình LTR (%s) — dùng xếp hạng tuyến tính", error)
            return None

        _cache.update({"booster": booster, "meta": meta, "path": model_path})
        logger.info(
            "Đã nạp mô hình LTR %s (%d nhóm huấn luyện, nDCG@%s = %s)",
            model_path,
            meta.get("groups", -1),
            meta.get("k", "?"),
            meta.get("ndcgMean", "?"),
        )
        return booster


def available() -> bool:
    return load() is not None


def info() -> dict[str, Any]:
    """Siêu dữ liệu mô hình để lộ ra endpoint quan sát."""
    booster = load()
    meta = _cache["meta"] or {}
    return {
        "available": booster is not None,
        "path": str(_cache["path"]) if _cache["path"] else None,
        "trainedGroups": meta.get("groups"),
        "trainedRows": meta.get("rows"),
        "ndcgMean": meta.get("ndcgMean"),
        "ndcgStd": meta.get("ndcgStd"),
        "insufficientData": meta.get("insufficientData"),
        "featureCount": len(FEATURE_NAMES),
    }


def score(
    candidates: Sequence[Mapping[str, Any]],
    category_boost: Mapping[str, float] | None = None,
    graph_boost: set[str] | frozenset[str] | None = None,
) -> list[float] | None:
    """Điểm LTR cho từng candidate, hoặc ``None`` nếu chưa có mô hình.

    Điểm LambdaMART là số thực không giới hạn và chỉ có ý nghĩa SO SÁNH trong
    cùng một truy vấn — không đem so giữa hai truy vấn khác nhau, và cũng không
    cùng thang với điểm tuyến tính [0,1].
    """
    booster = load()
    if booster is None or not candidates:
        return None
    matrix = [
        extract_features(candidate, category_boost=category_boost, graph_boost=graph_boost)
        for candidate in candidates
    ]
    try:
        predictions = booster.predict(matrix)
    except Exception as error:  # noqa: BLE001 - không để lỗi suy luận làm hỏng tìm kiếm
        logger.warning("Suy luận LTR lỗi (%s) — rơi về xếp hạng tuyến tính", error)
        return None
    return [float(value) for value in predictions]


def reset_cache() -> None:
    """Buộc nạp lại ở lần gọi sau (dùng trong test và sau khi huấn luyện)."""
    with _lock:
        _cache.update({"loaded": False, "booster": None, "meta": None, "path": None})
