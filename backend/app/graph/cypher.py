"""Câu Cypher chỉ-đọc dùng cho gợi ý. Tách khỏi client để test được mà không
cần Neo4j. Tất cả tham số hóa (``$param``) để tránh injection."""

from __future__ import annotations

# "Người có hành vi tương tự cũng thích": các phiên khác từng click trùng POI
# với phiên hiện tại, rồi lấy những POI khác mà họ click — collaborative filtering
# trên đồ thị đồng-click. Loại POI mà phiên hiện tại đã click.
ALSO_LIKED = """
MATCH (s:Session {id: $session_id})-[:CLICKED]->(:Poi)<-[:CLICKED]-(other:Session)
WHERE other.id <> $session_id
MATCH (other)-[:CLICKED]->(rec:Poi)
WHERE NOT (s)-[:CLICKED]->(rec)
RETURN rec.id AS poi_id,
       count(DISTINCT other) AS shared,
       count(*) AS weight
ORDER BY shared DESC, weight DESC
LIMIT $limit
"""

# "Địa điểm liên quan": hàng xóm SIMILAR_TO của một tập POI hạt giống (ví dụ POI
# đang trending hoặc user vừa xem), cộng dồn trọng số cạnh.
RELATED_POIS = """
MATCH (p:Poi)-[r:SIMILAR_TO]->(rec:Poi)
WHERE p.id IN $poi_ids AND NOT rec.id IN $poi_ids
RETURN rec.id AS poi_id, sum(r.weight) AS weight
ORDER BY weight DESC
LIMIT $limit
"""

# Đếm nút/cạnh để kiểm chứng đồng bộ.
GRAPH_STATS = """
MATCH (p:Poi) WITH count(p) AS pois
MATCH (s:Session) WITH pois, count(s) AS sessions
OPTIONAL MATCH (:Session)-[c:CLICKED]->(:Poi) WITH pois, sessions, count(c) AS clicked
OPTIONAL MATCH (:Poi)-[sim:SIMILAR_TO]->(:Poi)
RETURN pois, sessions, clicked, count(sim) AS similar
"""
