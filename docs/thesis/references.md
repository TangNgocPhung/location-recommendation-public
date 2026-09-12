# Tài liệu tham khảo

Định dạng IEEE. **Kiểm tra lại với quy định của khoa trước khi nộp** — một số
trường yêu cầu APA hoặc mẫu riêng, và cách viết tên tác giả Việt hoá cũng khác nhau
giữa các đơn vị.

Mỗi mục ghi kèm dòng *Dùng ở đâu* để khi bảo vệ chỉ ra được ngay vị trí trong hệ
thống. Trích dẫn không phải thủ tục: hằng số `k = 60` trong công thức RRF ở
`docs/layer-4-retrieval.md` là giá trị lấy từ bài gốc [2], dùng mà không dẫn nguồn
là lỗi liêm chính học thuật.

## Thuật toán truy xuất và xếp hạng

[1] S. E. Robertson and H. Zaragoza, "The Probabilistic Relevance Framework:
BM25 and Beyond," *Foundations and Trends in Information Retrieval*, vol. 3,
no. 4, pp. 333–389, 2009. doi: 10.1561/1500000019.
*Dùng ở đâu:* kênh full-text trong `backend/app/search/query.py` (`bm25_body`);
OpenSearch cài đặt BM25 làm hàm tính điểm mặc định.

[2] G. V. Cormack, C. L. A. Clarke, and S. Buettcher, "Reciprocal Rank Fusion
Outperforms Condorcet and Individual Rank Learning Methods," in *Proc. 32nd
Int. ACM SIGIR Conf. on Research and Development in Information Retrieval
(SIGIR '09)*, Boston, MA, USA, 2009, pp. 758–759. doi: 10.1145/1571941.1572114.
*Dùng ở đâu:* `backend/app/search/fusion.py`. Hằng số `k = 60` trong
`docs/layer-4-retrieval.md` lấy trực tiếp từ bài này; tác giả chọn 60 theo thực
nghiệm để hạ ảnh hưởng của các thứ hạng đầu mà không cần chuẩn hoá điểm giữa các
kênh có thang đo khác nhau — đúng vấn đề gặp phải khi gộp BM25, geo và k-NN.

[3] C. J. C. Burges, "From RankNet to LambdaRank to LambdaMART: An Overview,"
Microsoft Research, Redmond, WA, USA, Tech. Rep. MSR-TR-2010-82, 2010.
*Dùng ở đâu:* cơ sở lý thuyết cho mô hình Learning-to-Rank (bước A6 của lộ
trình). Hiện hệ thống dùng tổ hợp tuyến tính có trọng số tay ở
`backend/app/ranking.py`; LambdaMART là bước thay thế có học từ dữ liệu.

[4] Y. A. Malkov and D. A. Yashunin, "Efficient and Robust Approximate Nearest
Neighbor Search Using Hierarchical Navigable Small World Graphs," *IEEE Trans.
Pattern Analysis and Machine Intelligence*, vol. 42, no. 4, pp. 824–836, 2020.
doi: 10.1109/TPAMI.2018.2889473.
*Dùng ở đâu:* kênh vector k-NN. Trường `knn_vector` của OpenSearch dùng HNSW làm
thuật toán tìm láng giềng xấp xỉ mặc định.

## Đánh giá chất lượng

[5] K. Järvelin and J. Kekäläinen, "Cumulated Gain-Based Evaluation of IR
Techniques," *ACM Trans. Information Systems*, vol. 20, no. 4, pp. 422–446,
Oct. 2002. doi: 10.1145/582415.582418.
*Dùng ở đâu:* `ndcg_at_k` trong `backend/app/evaluation.py` và relevance gate
trong CI. Đây là nguồn của công thức DCG có chiết khấu logarit và cách chuẩn hoá
theo xếp hạng lý tưởng.

[6] T. Joachims, L. Granka, B. Pan, H. Hembrooke, and G. Gay, "Accurately
Interpreting Clickthrough Data as Implicit Feedback," in *Proc. 28th Int. ACM
SIGIR Conf. (SIGIR '05)*, Salvador, Brazil, 2005, pp. 154–161.
doi: 10.1145/1076034.1076063.
*Dùng ở đâu:* cơ sở cho việc ghi impression ở bước A2. Bài này chỉ ra click chỉ
diễn giải được khi biết người dùng đã NHÌN THẤY những gì — tức phải có cả mẫu
âm. Đây cũng là lý do mỗi impression mang theo `rank`: hiệu ứng position bias mà
bài mô tả chỉ đo được khi biết vị trí hiển thị.

## Hạ tầng dữ liệu không gian

[7] Uber Technologies, "H3: A Hexagonal Hierarchical Geospatial Indexing
System." [Online]. Available: https://h3geo.org/docs/ (truy cập 10/09/2026).
*Dùng ở đâu:* `backend/app/poi_features.py` (`h3_cells`), các cột `h3_r7`,
`h3_r8`, `h3_r9` trong bảng `pois`.

[8] PostGIS Project Steering Committee, "PostGIS Manual." [Online]. Available:
https://postgis.net/documentation/ (truy cập 10/09/2026).
*Dùng ở đâu:* `ST_DWithin`, `ST_Distance`, kiểu `GEOGRAPHY(POINT, 4326)` và chỉ
mục GiST trong `0001_initial_schema.py`.

[9] OpenSearch Project, "OpenSearch Documentation." [Online]. Available:
https://opensearch.org/docs/latest/ (truy cập 10/09/2026).
*Dùng ở đâu:* toàn bộ gói `backend/app/search/` — analyzer bỏ dấu, `knn_vector`,
`geo_distance`.

[10] Neo4j Inc., "Cypher Query Language Reference." [Online]. Available:
https://neo4j.com/docs/cypher-manual/current/ (truy cập 10/09/2026).
*Dùng ở đâu:* `backend/app/graph/` — đồng bộ đồ thị và truy vấn gợi ý
"người có hành vi tương tự cũng thích".

[11] Redis Ltd., "Redis Streams." [Online]. Available:
https://redis.io/docs/latest/develop/data-types/streams/ (truy cập 10/09/2026).
*Dùng ở đâu:* hàng đợi sự kiện giữa API và `backend/app/stream_worker.py`
(consumer group, `XADD`/`XREADGROUP`).

## Dữ liệu và giấy phép

[12] OpenStreetMap Foundation, "Copyright and License." [Online]. Available:
https://www.openstreetmap.org/copyright (truy cập 10/09/2026).

[13] Open Data Commons, "Open Database License (ODbL) v1.0." [Online].
Available: https://opendatacommons.org/licenses/odbl/1-0/ (truy cập 10/09/2026).
*Dùng ở đâu:* dữ liệu POI nhập qua Overpass API. ODbL là giấy phép
**share-alike**: bắt buộc ghi công và bắt buộc chia sẻ lại cơ sở dữ liệu phái
sinh dưới cùng giấy phép. Attribution được hiển thị trên bản đồ ở
`frontend/components/location-explorer.tsx`; phần share-alike cần nêu rõ trong
báo cáo nếu có ý định phân phối dữ liệu.

---

## Cách chèn trích dẫn vào bài

Đánh số `[n]` ngay sau mệnh đề dùng đến nguồn, không dồn hết xuống cuối đoạn:

> Ba kênh truy xuất được gộp bằng Reciprocal Rank Fusion [2] với `k = 60` theo
> khuyến nghị của tác giả.

Các chỗ trong repo đã được chèn số: `docs/layer-3.md`, `docs/layer-4-retrieval.md`,
`docs/layer-2-graph.md`, `docs/data-model.md`, `docs/quality-gates.md`. Khi chuyển
nội dung sang bản Word, giữ nguyên số cho khớp danh mục này.
