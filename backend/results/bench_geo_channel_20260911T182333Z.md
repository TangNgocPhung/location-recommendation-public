# Độ trễ kênh không gian — geo_distance so với vành hexagon H3

- Chỉ mục: **3010 document**
- Mỗi cấu hình: 5 vòng làm nóng (vứt) + 40 lần đo, hai kênh xen kẽ
- `size` mỗi kênh: 150

| Tâm | Bán kính | Ô H3 | geo TB | H3 TB | Chênh | Nhiễu nền | Kết luận | Recall | Dư |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| Bến Thành | 500 m | r9-k3-37 | 12.71 ms | 12.25 ms | -0.46 ms | 0.36 ms | đáng kể | 1.0 | 0 |
| Bến Thành | 1000 m | r9-k5-91 | 12.3 ms | 11.61 ms | -0.69 ms | 0.02 ms | đáng kể | 1.0 | 0 |
| Bến Thành | 3000 m | r9-k10-331 | 12.41 ms | 13.08 ms | +0.67 ms | 0.52 ms | đáng kể | 1.0 | 0 |
| Bến Thành | 5000 m | r8-k7-169 | 9.99 ms | 10.77 ms | +0.78 ms | 0.32 ms | đáng kể | 1.0 | 0 |
| Thảo Điền | 500 m | r9-k3-37 | 7.53 ms | 8.02 ms | +0.49 ms | 0.28 ms | đáng kể | 1.0 | 25 |
| Thảo Điền | 1000 m | r9-k5-91 | 7.05 ms | 7.72 ms | +0.67 ms | 0.08 ms | đáng kể | 1.0 | 41 |
| Thảo Điền | 3000 m | r9-k10-331 | 13.53 ms | 12.79 ms | -0.74 ms | 1.32 ms | trong nhiễu | 1.0 | 0 |
| Thảo Điền | 5000 m | r8-k7-169 | 11.39 ms | 11.63 ms | +0.24 ms | 0.54 ms | trong nhiễu | 1.0 | 0 |
| Phú Nhuận | 500 m | r9-k3-37 | 6.1 ms | 9.61 ms | +3.51 ms | 0.04 ms | đáng kể | 1.0 | 117 |
| Phú Nhuận | 1000 m | r9-k5-91 | 9.33 ms | 9.82 ms | +0.49 ms | 0.22 ms | đáng kể | 1.0 | 41 |
| Phú Nhuận | 3000 m | r9-k10-331 | 12.34 ms | 12.66 ms | +0.32 ms | 0.42 ms | trong nhiễu | 1.0 | 0 |
| Phú Nhuận | 5000 m | r8-k7-169 | 12.75 ms | 13.09 ms | +0.34 ms | 0.76 ms | trong nhiễu | 1.0 | 0 |
| Thủ Đức | 500 m | r9-k3-37 | 5.39 ms | 4.5 ms | -0.89 ms | 0.2 ms | đáng kể | 1.0 | 3 |
| Thủ Đức | 1000 m | r9-k5-91 | 4.92 ms | 4.59 ms | -0.33 ms | 0.15 ms | đáng kể | 1.0 | 10 |
| Thủ Đức | 3000 m | r9-k10-331 | 11.0 ms | 12.25 ms | +1.25 ms | 0.31 ms | đáng kể | 1.0 | 12 |
| Thủ Đức | 5000 m | r8-k7-169 | 12.3 ms | 11.86 ms | -0.44 ms | 0.38 ms | đáng kể | 1.0 | 0 |

## Kết luận đọc thẳng từ bảng trên

- Trung bình: geo_distance **10.06 ms**, H3 **10.39 ms**, nhiễu nền trung bình **0.37 ms**.
- Số cấu hình có chênh lệch vượt nhiễu nền: **12/16** (5 nghiêng về H3, 7 nghiêng về geo_distance).
- Recall của vành hexagon so với geo_distance: **1.00 - 1.00**.

> Cột `Nhiễu nền` là chênh lệch giữa HAI lần đo cùng một truy vấn
> geo_distance trong cùng một vòng. Chênh lệch giữa hai kênh chỉ được
> coi là đáng kể khi nó lớn hơn con số này - nếu không, thứ đang được
> đo là tải máy chứ không phải cách lọc.

> Cột `Recall` là phần kết quả của geo_distance mà vành hexagon giữ được;
> cột `Dư` là số POI vành trả thêm vì nó phủ trùm hình tròn. Phần dư này
> bị `hydrate_candidates` cắt lại bằng `ST_DWithin`, nên nó tốn băng thông
> chứ không làm sai kết quả.