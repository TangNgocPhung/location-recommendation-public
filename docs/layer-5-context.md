# Spatio-Temporal Enricher — Bước 5

Sau khi truy xuất và trước khi xếp hạng, mỗi candidate được làm giàu bằng ngữ
cảnh không gian–thời gian. Toàn bộ dùng dữ liệu sẵn có (giờ mở, event log) và
đồng hồ hệ thống — **không gọi API ngoài** — phù hợp đồ án chạy trên laptop.

Mã nguồn: [`backend/app/spatio_temporal.py`](../backend/app/spatio_temporal.py),
trạng thái giờ mở ở [`backend/app/opening_hours.py`](../backend/app/opening_hours.py)
(`opening_status`). Enricher chạy trong `ranking.rank_pois`, ngay trước `rerank`.

## Tín hiệu được thêm

| Trường (mỗi candidate) | Ý nghĩa |
|---|---|
| `openNow` / `closesInMinutes` / `opensInMinutes` | Đang mở, còn bao phút thì đóng, hoặc bao phút nữa mở (từ `opening_hours`). |
| `timeContext.bucket` / `isWeekend` | Buổi trong ngày (morning/noon/afternoon/evening/night) và ngày thường/cuối tuần. |
| `timeContext.categoryMatchesTime` | Loại địa điểm có hợp thời điểm không (cà phê buổi sáng, quán bar buổi tối…). |
| `popularityWindows.w15/w1h/w24h` | Số tương tác trong cửa sổ 15 phút / 1 giờ / 24 giờ (từ `ingestion_events`), chỉ đếm `poi_click`, `poi_dwell`, `navigation_start`, `review` — xem `POPULARITY_EVENT_TYPES`. |
| `recencyScore` ∈ [0,1] | Popularity thời gian thực, thiên về gần đây: `3·w15 + 1.5·w1h + 0.5·w24h`, chuẩn hóa theo max trong tập ứng viên. |
| `contextScore` ∈ [0,1] | `0.6·(giờ mở) + 0.4·(hợp thời điểm)`; chưa rõ giờ = trung tính 0.5, không phạt oan. |
| `etaMinutes.walk/motorbike/car` | ETA ước lượng theo phương tiện từ khoảng cách (tạm theo đường chim bay đến khi có routing engine). |

## Ảnh hưởng tới xếp hạng

`ranking.rerank` cộng thêm hai tín hiệu vào công thức tuyến tính:

```
score += w_recency · recencyScore + w_context · contextScore
```

Trọng số mặc định (tổng ≈ 1.0): text 0.26 · spatial 0.24 · rating 0.12 ·
popularity 0.08 · trending 0.08 · **recency 0.12** · **context 0.10**.

Kiểm chứng thật: bơm 8 `poi_click` cho một quán đang xếp #6 → `recencyScore`
lên 1.0, `popularityWindows` = {15p:8, 1h:8, 24h:8}, quán leo lên #3.

> **`poi_impression` CỐ Ý không được đếm ở đây.** Sau bước A2 mỗi POI được hiển
> thị sinh một impression (~50 cho mỗi click). Đếm chúng sẽ biến `recencyScore`
> thành "POI này được hiển thị bao nhiêu lần" — mà số lần hiển thị lại do chính
> xếp hạng quyết định, tạo vòng lặp tự củng cố: xếp cao → được hiển thị →
> recency cao → xếp cao hơn nữa. Trọng số recency là 0.12, lớn hơn cả ctr 0.08.

## Giới hạn hiện tại & bước sau

- ETA đang theo đường chim bay; routing thực (geometry + ETA theo tuyến) sẽ
  thay ở bước Routing/MapLibre.
- Mật độ giao thông và thời tiết (cần API ngoài, cache Redis) chưa tích hợp —
  để dành bước sau; kiến trúc enricher đã sẵn chỗ cắm thêm tín hiệu.
- `contextScore`/`recencyScore` là feature tường minh, sẽ trở thành đầu vào cho
  LightGBM LTR ở bước xếp hạng học máy.
