# Quality gates và cách đo

Các ngưỡng dưới đây là baseline cho môi trường test local/CI, không phải cam
kết SLA production. Khi dataset và tải tăng, mọi thay đổi ngưỡng phải được ghi
lại cùng kết quả benchmark.

| Chỉ số | Gate hiện tại | Cách đo |
|---|---:|---|
| Search latency p95 | `<= 500 ms` ở 50 request tuần tự sau warm-up | `python scripts/benchmark_search.py` |
| Search error rate | `<= 1%` trong benchmark | cùng script latency |
| Event processing lag | `<= 5 giây` từ API accept đến `processed` | E2E polling ingestion status |
| Relevance | mean `NDCG@10 >= 0.75` [5] trên judgments đã version hóa | `python scripts/evaluate_relevance.py` |
| Relevance (bổ sung) | MRR, P@10, R@10, MAP@10 — báo cáo, chưa đặt ngưỡng chặn | cùng script, ghi ra `backend/results/relevance_<timestamp>.json` |
| Backend coverage | CI floor `>= 20%`, tăng dần và không hạ; mục tiêu `>= 80%` | `pytest --cov=app` |
| Cache hit rate | `>= 60%` với workload có ít nhất 30% truy vấn lặp | kích hoạt khi query cache được thêm; chưa enforce ở MVP hiện tại |

## Chạy local

Từ thư mục gốc:

```powershell
docker compose --env-file config/development.env up -d --build --wait
```

Từ `backend/`, cài dependencies test và chạy:

```powershell
python -m pip install -r requirements-dev.txt
pytest tests/unit --cov=app --cov-report=term-missing
$env:RUN_INTEGRATION = "1"
$env:API_BASE_URL = "http://localhost:8000"
pytest tests/integration
$env:RUN_E2E = "1"
$env:GATEWAY_BASE_URL = "http://localhost:8081"
pytest tests/e2e
python scripts/evaluate_relevance.py
python scripts/benchmark_search.py
```

Integration và E2E được skip mặc định để unit test không phụ thuộc Docker.
Judgments trong `tests/fixtures/relevance_judgments.json` phải được review như
code: thêm query mới cùng nhãn relevance, không sửa nhãn chỉ để làm điểm cao.

Mọi lần chạy đều ghi kết quả kèm mốc thời gian vào `backend/results/`. Bảng số
liệu trong báo cáo phải truy ngược được về một file cụ thể ở đó; đừng chép số từ
màn hình. File kết quả cũng ghi lại `retrievalBackend` — nếu là `postgis` thì lần
đo đó là của đường dự phòng, không phải của truy xuất đa kênh, và không dùng
được để kết luận về kiến trúc.

Đo độ trễ ở nhiều mức tải, không chỉ một luồng:

```powershell
$env:BENCHMARK_CONCURRENCY = "1";  python scripts/benchmark_search.py
$env:BENCHMARK_CONCURRENCY = "10"; python scripts/benchmark_search.py
$env:BENCHMARK_CONCURRENCY = "50"; python scripts/benchmark_search.py
```

## Tiêu chí gán nhãn relevance

Phải trả lời được câu hỏi "nhãn ở đâu ra?". Thang 4 mức, gán theo góc nhìn của
một người dùng đang đứng tại tọa độ truy vấn:

| Mức | Nghĩa | Tiêu chí |
|---:|---|---|
| 3 | Hoàn hảo | Đúng loại địa điểm người dùng hỏi, trong bán kính hợp lý, đang mở hoặc không có ràng buộc giờ. Nếu truy vấn có địa danh thì phải đúng khu vực đó. |
| 2 | Liên quan | Đúng loại nhưng có một điểm trừ rõ ràng: xa hơn đáng kể, đang đóng cửa, hoặc thuộc khu vực lân cận chứ không đúng địa danh được hỏi. |
| 1 | Chấp nhận được | Loại gần đúng (hỏi "cà phê" trả về quán ăn có phục vụ cà phê), hoặc đúng loại nhưng ở rất xa. Người dùng không bực nhưng cũng không dùng. |
| 0 | Không liên quan | Sai loại, hoặc ngoài bán kính truy vấn. Không cần ghi vào file — thiếu nghĩa là 0. |

Quy ước bắt buộc:

- **Gán nhãn trước khi xem thứ hạng hệ thống trả về.** Nhìn thứ hạng rồi mới gán
  là tự xác nhận chính mình, và mọi con số sau đó vô nghĩa.
- Nhãn gắn với **cặp (truy vấn, POI)**, không phải với POI. Cùng một quán có thể
  là 3 cho "cà phê Quận 1" và 0 cho "phở".
- Mỗi truy vấn ghi trường `group` để phân tích theo nhóm. Các nhóm bắt buộc phải
  có: `ngắn`, `địa danh`, `không dấu`, `sai chính tả`, `theo giờ`, `đa tâm`.
- Ngưỡng "liên quan" cho các chỉ số nhị phân (MRR, P, R, MAP) mặc định là `>= 1`.
  Đặt `RELEVANT_THRESHOLD=2` để đọc theo nghĩa chặt hơn; báo cáo nên có cả hai.

Dựng khung file nhãn bằng script, rồi điền tay cột điểm:

```powershell
python scripts/build_judgment_template.py
```

Script gọi API thật cho từng truy vấn mẫu và ghi ra danh sách POI kèm tên, địa
chỉ, khoảng cách — việc còn lại chỉ là chấm 0–3 cho từng dòng. Nó **không tự gán
nhãn**: tự chấm rồi tự đo là vòng luẩn quẩn.
