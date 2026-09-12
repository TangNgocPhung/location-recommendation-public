"""Job materialize: tính feature offline (Postgres) rồi đẩy sang online store
(Redis) để serving đọc độ trễ thấp.

Chạy: ``python -m app.features.materialize`` (``--views user_profile,region_ctr``
để chọn view). Đây là bước "materialization" của một feature store.
"""

from __future__ import annotations

import argparse
import logging

from ..config import settings
from . import offline, online

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("nearby-features-materialize")

_COMPUTERS = {
    "user_profile": offline.compute_user_profiles,
    "region_ctr": offline.compute_region_ctr,
    "poi_embedding": offline.compute_poi_embeddings,
}


def materialize(views: list[str] | None = None) -> dict[str, int]:
    selected = views or list(_COMPUTERS)
    written: dict[str, int] = {}
    for name in selected:
        compute = _COMPUTERS.get(name)
        if compute is None:
            logger.warning("Bỏ qua view không có trong registry: %s", name)
            continue
        rows = compute()
        count = online.write_features(name, rows)
        written[name] = count
        logger.info("Materialize %s: %d entity (tính được %d)", name, count, len(rows))
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize feature offline -> online")
    parser.add_argument(
        "--views",
        default="",
        help="Danh sách view, phân tách bằng dấu phẩy (mặc định: tất cả)",
    )
    args = parser.parse_args()
    views = [v.strip() for v in args.views.split(",") if v.strip()] or None
    materialize(views)


if __name__ == "__main__":
    main()
