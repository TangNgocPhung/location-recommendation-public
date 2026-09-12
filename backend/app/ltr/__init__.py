"""Learning-to-Rank: đặc trưng, tập huấn luyện, huấn luyện và suy luận.

Bốn module tách bạch theo vòng đời:

- ``features``  — định nghĩa vector đặc trưng. Nguồn sự thật DUY NHẤT, dùng
  chung cho cả lúc huấn luyện lẫn lúc phục vụ, để không lệch train/serve.
- ``dataset``   — dựng tập huấn luyện từ hai nguồn nhãn (phán quyết liên quan
  do người gán, và log click).
- ``train``     — huấn luyện LightGBM ``LGBMRanker`` mục tiêu ``lambdarank``.
- ``model``     — nạp mô hình đã lưu và chấm điểm lúc phục vụ; không có mô hình
  thì im lặng trả ``None`` để tầng gọi dùng công thức tuyến tính.
"""
