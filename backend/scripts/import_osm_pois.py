"""Import real POIs from OpenStreetMap/Overpass into the canonical POI store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import settings
from app.poi_import import (
    fetch_overpass_elements,
    import_osm_elements,
    parse_bbox,
    refresh_osm_districts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", default=settings.osm_bbox, help="south,west,north,east")
    parser.add_argument("--max-pois", type=int, default=settings.osm_max_pois)
    parser.add_argument("--input", type=Path, help="read an Overpass JSON snapshot instead")
    parser.add_argument("--save-raw", type=Path, help="save the fetched Overpass JSON snapshot")
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="normalize districts from persisted source lineage without network access",
    )
    args = parser.parse_args()

    if args.refresh_existing:
        print(json.dumps({"updated": refresh_osm_districts(settings.database_url)}))
        return

    bbox = parse_bbox(args.bbox)
    if args.input:
        elements = json.loads(args.input.read_text(encoding="utf-8")).get("elements", [])
    else:
        elements = fetch_overpass_elements(settings.overpass_url, bbox)
        if args.save_raw:
            args.save_raw.parent.mkdir(parents=True, exist_ok=True)
            args.save_raw.write_text(
                json.dumps({"elements": elements}, ensure_ascii=False),
                encoding="utf-8",
            )

    stats = import_osm_elements(settings.database_url, elements, bbox, args.max_pois)
    print(json.dumps(stats, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
