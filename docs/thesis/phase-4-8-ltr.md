# Phase 4 → 8 — Learning-to-Rank: đã dựng xong đường ống, đang chờ nhãn

**Ngày:** 2026-09-11 · Tiếp nối [Phase 3 — Kiểm toán feature vector](phase-3-feature-audit.md)

## Tóm tắt một đoạn

Toàn bộ đường ống Phase 4 → 8 đã chạy được từ đầu đến cuối: dựng tập huấn
luyện, huấn luyện LambdaMART, tích hợp vào API, so sánh với baseline, và thang
ablation 5 bậc. Cái còn thiếu **không phải code mà là nhãn**: chỉ có 3 truy vấn
đã gán nhãn (2 dùng được) và 6/36 nhóm click có nhãn dương. Script vì vậy **từ
chối ghi `model.txt`** và mọi báo cáo đều tự dán cảnh báo khi bậc LTR không
thực sự chạy. Khung 400 cặp đã sinh sẵn, chờ gán nhãn tay.

## 0. Hai lỗi chặn đã sửa trước

Phase 3 chỉ ra hai lỗi làm mọi số đo sai lệch; cả hai đã sửa trước khi làm LTR.

### `rating`: NULL ≠ 0 điểm

Migration [`0008_nullable_rating`](../../backend/migrations/versions/0008_nullable_rating.py)
cho `pois.rating` nhận NULL và chuyển 2982 giá trị 0 thành NULL. Xác nhận sau
khi chạy: 2982 NULL / 28 có giá trị / 3010 tổng.

`ranking.rerank` nay bỏ qua số hạng thiếu dữ liệu rồi **chuẩn hóa theo tổng
trọng số thực sự áp dụng được**, nên POI chưa ai đánh giá không còn bị xếp
dưới POI bị chấm 0/5. Điểm số giờ nằm trong `[0,1]` thay vì `[0,1.18]`.

### `textScore`: điểm khớp văn bản, không phải điểm RRF

`enrichment.hydrate_candidates` nay nhận `bm25_scores` từ
`retrieval.multi_channel_candidates` và đặt `textScore` theo **điểm BM25 thô đã
chuẩn hóa**, còn điểm RRF chuyển sang trường riêng `fusionScoreNorm`.

Tác động đo được trên cùng 3 truy vấn: cấu hình đầy đủ tăng từ **nDCG 0.4720 →
0.6406**. Nguyên nhân đúng như Phase 3 dự đoán — trước đây kênh geo và trending
bị cộng hai lần (một lần nằm sẵn trong RRF, một lần qua `w_spatial`/`w_trending`).

## 1. Phase 3 (bổ sung) — hai tín hiệu chết đã được nối dây

`/api/v1/search` vốn nhận `session_id` nhưng không dùng, nên `graph` (0.10) và
`category_boost` luôn bằng 0 trên đường tìm kiếm. Nay
[`api.py`](../../backend/app/api.py) tính `session_profile` → `category_boost`
và `graph_candidate_ids` → `graph_boost` rồi truyền xuống, giống
`/recommendations`.

## 2. Phase 4 — tập huấn luyện

[`app/ltr/dataset.py`](../../backend/app/ltr/dataset.py) dựng nhóm từ hai nguồn.

### Nguồn chính — phán quyết liên quan (`judgment`)

Nhãn 0–3 do người gán; ứng viên lấy bằng chính pipeline đang chạy. Kết quả hiện
tại: **3 nhóm, 300 cặp, chỉ 2 nhóm dùng được** (một truy vấn không có POI liên
quan nào lọt top-100 — bản thân đó đã là một phát hiện về tầng truy xuất).

### Nguồn phụ — log click (`click`)

Đo trên log thật:

| Chỉ số | Giá trị |
|---|---:|
| Nhóm `request_id` có impression | 36 |
| Tổng impression | 943 |
| **Nhóm có ít nhất một click** | **6** |
| Nhóm suy ra được tâm tìm kiếm | 14 |
| Phiên khác nhau | 12 |

Nhóm không có nhãn dương bị `lambdarank` bỏ qua, nên **tập click hiện tại chỉ
còn vài nhóm** — không đủ huấn luyện. Ba khiếm khuyết đã ghi trong docstring và
phải nêu trong báo cáo:

1. **Không có ảnh chụp đặc trưng lúc hiển thị.** Log chỉ lưu
   `(request_id, rank, query)`. Các đặc trưng phụ thuộc thời gian phải tính lại
   ở hiện tại, nên không phải giá trị người dùng đã thấy.
2. **Position bias** chưa khử (không randomization, không propensity).
3. **Tọa độ tâm** phải suy từ sự kiện `search` gần nhất cùng phiên; 22/36 nhóm
   không suy ra được và bị bỏ.

### Độ phủ đặc trưng thực đo (300 dòng)

| Đặc trưng | Độ phủ | Ghi chú |
|---|---:|---|
| `price_level` | **0%** | 3010/3010 POI không có giá |
| `rating` | **5%** | đúng bằng tỉ lệ POI seed |
| `is_open` | 11% | 83% POI không có `opening_hours` |
| `bm25_score` | 53% | ứng viên vào qua kênh geo/vector không có điểm BM25 |
| `category_time_match` | 61% | loại ngoài dict 12 mục là trung tính |
| còn lại | 100% | |

Bảng này thuộc về báo cáo: một đặc trưng phủ 0% thì mô hình không học được gì
từ nó, và người đọc cần thấy con số đó thay vì chỉ thấy tên đặc trưng trong sơ đồ.

## 3. Phase 5 — mô hình

[`app/ltr/train.py`](../../backend/app/ltr/train.py) dùng **API gốc**
`lgb.train(objective="lambdarank")` chứ không phải `LGBMRanker`, để ảnh phục vụ
không phải cài scikit-learn (nó chỉ cần `lgb.Booster` để nạp mô hình).

Ba quyết định đáng nêu trong báo cáo:

- **Tách tập theo nhóm `qid`, không theo dòng.** Hai ứng viên cùng truy vấn
  tương quan rất mạnh; tách theo dòng cho ra nDCG khống.
- **k-fold thay vì một lần tách,** kèm độ lệch chuẩn — với vài chục nhóm, một
  lần tách cho con số dao động vô nghĩa.
- **Chặn cứng ở `MIN_GROUPS_FOR_CLAIM = 30`.** Dưới ngưỡng, script in báo cáo
  nhưng **không ghi `model.txt`** (trừ khi `--force`). Lý do: một khi file mô
  hình nằm đó thì mọi phép đo sau mặc định chạy qua nó.

Lần chạy thật: `nDCG@10 = 0.7076 ± 0.2924` trên 2 nhóm / 200 dòng → đúng như
thiết kế, **không ghi mô hình**. Con số này chỉ chứng minh đường ống thông.

### Đặc trưng cố ý loại khỏi mô hình

- `rank` / vị trí hiển thị — mô hình sẽ học thuộc chính bộ xếp hạng cũ.
- `source` (seed / openstreetmap) — chỉ cần học "seed = tốt" là đạt điểm cao mà
  không học gì về mức liên quan.
- `sponsored` — luật nghiệp vụ áp sau xếp hạng, không phải bằng chứng liên quan.

## 4. Phase 6 — tích hợp

- `POST /api/v1/search` nhận `"ranker": "linear" | "ltr"`.
- Phản hồi trả `"ranker"` là bộ xếp hạng **đã chạy thật**, không phải cái được
  yêu cầu — thiếu mô hình thì hệ thống rơi về `linear` một cách im lặng.
- `GET /api/v1/ltr/status` cho biết mô hình có nạp được không và huấn luyện
  trên bao nhiêu nhóm.
- [`app/ltr/model.py`](../../backend/app/ltr/model.py) **từ chối nạp** mô hình
  có danh sách đặc trưng khác `FEATURE_NAMES` hiện tại. Đây là lỗi im lặng
  nguy hiểm nhất của LTR: đổi thứ tự cột thì mô hình vẫn chạy, vẫn trả điểm,
  chỉ có điều điểm đó vô nghĩa.

Kiểm chứng: xóa `model.txt` → `/api/v1/search?ranker=ltr` vẫn trả kết quả bình
thường với `"ranker": "linear"`.

## 5. Phase 7 — Baseline vs Proposed

Script [`scripts/eval_rankers.py`](../../backend/scripts/eval_rankers.py). Hai
quyết định đo lường:

- **Truy xuất chạy một lần, dùng chung cho cả hai bộ xếp hạng** — chạy lại thì
  chênh lệch đo được lẫn cả nhiễu truy xuất.
- **Tách `retrievalMs` khỏi `rankMs`** — gộp chung thì chênh lệch thật của
  LambdaMART (dưới 1 ms) chìm trong thời gian truy vấn Postgres/OpenSearch
  (hàng trăm ms), và bảng sẽ kết luận sai rằng "hai bên như nhau".

Kết quả hiện tại (`results/eval_rankers_20260911T161726Z.md`): hai hàng giống
hệt nhau, kèm cảnh báo tự động rằng hàng "Proposed" thực ra chạy `linear`.
Truy xuất + làm giàu: 360 ms trung bình, 667 ms p95 — chi phí thật nằm ở đây,
không ở bộ xếp hạng (dưới 1 ms).

## 6. Phase 8 — thang ablation cộng dồn

[`scripts/run_ablation.py`](../../backend/scripts/run_ablation.py) đổi từ bảng
B0–B3 (trộn nhiều chiều cùng lúc) sang thang **A → E cộng dồn**, mỗi bậc thêm
đúng một thứ, nên chênh lệch giữa hai dòng liền nhau quy được cho thành phần
vừa thêm. Thêm cột P@10, độ trễ trung bình và p95.

| Bậc | Mô tả | nDCG@10 | MRR | MAP@10 | Recall@10 | Độ trễ TB |
|---|---|---:|---:|---:|---:|---:|
| A | PostGIS thuần | 0.6204 | 0.6667 | 0.5556 | 0.6667 | 118 ms |
| B | + OpenSearch BM25 + geo | 0.4098 | 0.5000 | 0.3037 | 0.5556 | 339 ms |
| C | + RRF 3 kênh | 0.4098 | 0.5000 | 0.3037 | 0.5556 | 287 ms |
| D | + Spatio-Temporal, đủ 9 tín hiệu | 0.6406 | 0.6667 | 0.6389 | 0.6667 | 416 ms |
| E | + LTR LambdaMART | *(= D, chưa có mô hình)* | | | | |

**Ba điều bảng này đang nói, và cả ba đều cần 400 cặp nhãn mới xác nhận được:**

1. Bậc B tụt so với A — chuyển sang OpenSearch làm giảm chất lượng trên 3 truy
   vấn này, đồng thời tăng độ trễ gần gấp ba.
2. Bậc C không đổi so với B — kênh vector hiện **không đóng góp gì**. Hợp lý:
   embedding đang là hashing trick, không mang ngữ nghĩa.
3. Bậc D vượt A — làm giàu ngữ cảnh bù lại được phần B/C đánh mất.

> **Cảnh báo bắt buộc giữ khi trích vào báo cáo:** 3 truy vấn là quá ít để kết
> luận bất cứ điều gì. Chênh lệch 0.02 giữa A và D nằm hoàn toàn trong khoảng
> nhiễu. Bảng này đúng về *cấu trúc thí nghiệm*, chưa đúng về *bằng chứng*.

## 7. Việc còn lại — theo thứ tự

1. **Gán nhãn 400 cặp.** Khung đã sinh lại bằng pipeline đã sửa:
   `tests/fixtures/judgment_template.json` — 40 truy vấn × 10 ứng viên, cột
   `grade` để trống. Chấm theo `docs/quality-gates.md`, lý tưởng là **không
   nhìn `serverRank`** khi chấm. Xong thì gộp vào `relevance_judgments.json`.
2. **Chạy lại** `train_ltr.py` → khi đủ 30 nhóm, script tự ghi `model.txt`.
3. **Chạy lại** `eval_rankers.py` và `run_ablation.py` → bậc E có số thật.
4. **Ghi vector đặc trưng vào log lúc phục vụ** để nguồn nhãn click hết bị lệch
   thời gian. Đây là điều kiện để dùng được click làm nhãn về sau.
5. **Quyết định về `region_ctr`**: materialize lại sang khóa `v2`, hoặc đặt
   `w_ctr = 0` và ghi rõ lý do. Hiện nó luôn bằng 0.

## Cách chạy

```bash
# Phase 4 + 5
docker compose -p nearby-dev run --rm --no-deps \
  -v "$PWD/backend/app:/app/app" -v "$PWD/backend/scripts:/app/scripts" \
  -v "$PWD/backend/results:/app/results" -v "$PWD/backend/tests:/app/tests" \
  backend python scripts/train_ltr.py --source judgment

# Phase 7
... backend python scripts/eval_rankers.py --k 10

# Phase 8
... backend python scripts/run_ablation.py

# Sinh lại khung gán nhãn (cần backend đang chạy)
... -e API_BASE_URL=http://backend:8000 backend-tests python scripts/build_judgment_template.py
```
