from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded exclusively from environment variables."""

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql://nearby_dev:nearby_dev_only@localhost:5432/nearby_dev"
    redis_url: str = "redis://localhost:6379/0"
    event_stream: str = "nearby:events:v1"
    consumer_group: str = "realtime-scoring"
    consumer_name: str = "worker-1"
    # Xử lý sự kiện lỗi (DLQ). Một sự kiện hỏng vĩnh viễn — ví dụ payload sai
    # định dạng — nếu cứ thử lại mãi sẽ chặn cả consumer group. Sau
    # max_delivery_attempts lần, đẩy sang dead-letter stream và đánh dấu 'failed'
    # để còn điều tra được, thay vì để nó kẹt im lặng trong Pending Entries List.
    max_delivery_attempts: int = 5
    # Sự kiện nằm trong PEL quá lâu nghĩa là consumer nhận nó đã chết. XAUTOCLAIM
    # giành lại cho consumer còn sống.
    reclaim_idle_ms: int = 60_000
    rate_limit_per_minute: int = 120
    # Số proxy tin cậy đứng trước API. 0 = KHÔNG tin X-Forwarded-For.
    #
    # Header này do client gửi được, nên tin mù là tạo lỗ hổng: ai cũng có thể
    # đặt một IP giả khác nhau cho mỗi request và lách sạch rate limit. Nhưng
    # bỏ qua nó cũng sai theo hướng ngược lại: sau gateway, MỌI người dùng ẩn
    # danh mang cùng IP của gateway nên dùng chung một hạn mức, một người có thể
    # làm cạn quota của tất cả.
    #
    # Cách đúng là đếm số hop: nginx dùng $proxy_add_x_forwarded_for, tức nó NỐI
    # THÊM IP của peer trực tiếp vào cuối danh sách. Với đúng một proxy tin cậy,
    # phần tử CUỐI là IP thật do nginx nhìn thấy, không giả được. Phần đầu danh
    # sách có thể do client bịa, nên không bao giờ lấy phần tử đầu.
    #
    # Chỉ đặt > 0 khi API KHÔNG thể truy cập trực tiếp từ ngoài, nếu không kẻ
    # tấn công gọi thẳng API và tự bịa header.
    trusted_proxy_hops: int = 0
    # Truy xuất đa kênh (Bước 3-4). "auto" dùng OpenSearch khi kết nối được và
    # tự rơi về PostGIS khi không; "opensearch" ép dùng OpenSearch; "postgis"
    # tắt hẳn OpenSearch (giữ nguyên hành vi cũ).
    search_backend: Literal["auto", "opensearch", "postgis"] = "auto"
    opensearch_url: str = ""
    opensearch_index: str = "nearby-pois"
    opensearch_knn_enabled: bool = True
    # Timeout (giây) cho truy vấn người dùng tới OpenSearch.
    #
    # Đo thực tế trên máy phát triển với 3.010 POI: truy vấn NGUỘI (ngay sau khi
    # dựng chỉ mục) mất 3.329 ms, truy vấn ấm 603-840 ms. Giá trị cũ là 1 giây,
    # nên truy vấn nguội LUÔN hỏng và truy vấn ấm chỉ cách ngưỡng 16% — mà
    # retrieve_candidates gọi ba kênh tuần tự, mỗi kênh một timeout riêng, nên
    # xác suất ít nhất một kênh vượt ngưỡng là rất cao.
    #
    # Hậu quả: hệ thống rơi về PostGIS gần như mọi lúc, và mọi số liệu đo được
    # là số của đường dự phòng chứ không phải của kiến trúc đa kênh. Đây đúng là
    # loại lỗi mà trường `retrievalBackend` được thêm vào để phơi ra.
    #
    # Vẫn giữ ngưỡng hữu hạn và circuit breaker: OpenSearch chết thật thì phải
    # rơi về PostGIS nhanh, không bắt người dùng chờ.
    opensearch_timeout_seconds: float = 3.0
    # Kênh không gian chạy những gì. "both" (mặc định) chạy cả vành hexagon H3
    # lẫn geo_distance như hai kênh riêng trong RRF — H3 lọc thô bằng một phép
    # tra `terms`, geo_distance lọc tinh theo đúng bán kính. "h3" và
    # "geo_distance" tắt bớt một kênh, để ablation tách được đóng góp của riêng
    # H3 — hexagon nằm sẵn trong DB không tự chứng minh nó có ích.
    geo_channel: Literal["both", "h3", "geo_distance"] = "both"
    # Trần số ô hexagon trong một truy vấn `terms`. Bán kính lớn tới mức vượt
    # trần (khoảng > 20 km) tự rơi về geo_distance — lúc đó vành hexagon không
    # còn rẻ hơn phép tính khoảng cách nữa.
    h3_ring_max_cells: int = 512
    # Số ứng viên gộp sau fusion trước khi re-rank (roadmap: ~200-500).
    candidate_pool_size: int = 300
    # OSRM tự dựng cho chỉ đường thật (lộ trình B16). Rỗng = tắt hẳn, nút chỉ
    # đường rơi về deep-link Google Maps như trước.
    #
    # TỰ DỰNG chứ không gọi máy chủ demo công cộng của dự án OSRM: máy chủ đó
    # cấm dùng cho ứng dụng thật và có giới hạn tần suất, nên một đồ án phụ
    # thuộc vào nó sẽ hỏng đúng lúc bảo vệ nếu mạng chập hoặc bị chặn.
    osrm_url: str = ""
    # Weather & Traffic Density Injection (Spatio-Temporal Enricher).
    #
    # Tắt được vì hai lý do thực tế: đo độ trễ sạch (thời tiết là một lần gọi
    # mạng ngoài, cache nguội mất tới 1,5 giây) và chạy ablation để tách đóng
    # góp của từng tín hiệu.
    weather_enabled: bool = True
    traffic_enabled: bool = True
    # Spatial Knowledge Graph (Neo4j). "auto" dùng khi kết nối được và tự bỏ qua
    # khi không; "off" tắt hẳn (giữ hành vi cũ).
    graph_backend: Literal["auto", "off"] = "auto"
    neo4j_url: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    # Google Places API (New) — nguon danh gia that cho POI nhap tu OSM, vi
    # OpenStreetMap khong co truong rating. De rong thi buoc lam giau bi bo
    # qua, he thong van chay binh thuong voi rating NULL.
    #
    # Key nay tinh phi theo tung truy van va phai la key CUA BAN. Xem
    # `app/poi_ratings.py` ve gioi han luu tru theo dieu khoan cua Google.
    google_maps_api_key: str = ""
    # Ảnh địa điểm lấy từ Wikimedia Commons (`app/photos.py`). OSM không có
    # trường ảnh, không có GOOGLE_MAPS_API_KEY, và Static Maps của MapTiler trả
    # 403 — Commons là nguồn duy nhất gọi được.
    #
    # Tắt được vì hai lý do thực tế: đo độ trễ sạch của trang chi tiết (dò ảnh
    # là nhiều lần gọi mạng ngoài, mỗi lần cách nhau tối thiểu một giây vì giới
    # hạn tần suất của Wikimedia), và chạy được khi máy không ra Internet. Tắt
    # thì endpoint ảnh trả `status='unavailable'` — "chưa dò được", KHÔNG phải
    # "địa điểm này không có ảnh".
    photos_enabled: bool = True
    wikimedia_timeout_seconds: float = 6.0
    # Trần thời gian cho MỘT lần dò ảnh nguội, tính cả lúc xếp hàng chờ giãn
    # nhịp chứ không chỉ lúc gọi mạng.
    #
    # Không có nó thì tệ nhất là 4 lần gọi Commons × 3 lượt thử × (1 giây giãn
    # nhịp + wikimedia_timeout_seconds) cộng backoff ≈ 96 giây, và suốt 96 giây
    # đó request GIỮ một luồng trong threadpool dùng chung của FastAPI — endpoint
    # ảnh là `def` nên mọi endpoint đồng bộ khác chia nhau đúng pool ấy.
    #
    # 12,0 vì đường vui vẻ đo được là 3,1 giây — rộng gấp gần bốn lần. Hết giờ
    # đọc là "chưa dò được" (`unavailable`) chứ KHÔNG BAO GIỜ là "không có ảnh".
    photo_fetch_budget_seconds: float = 12.0
    # Bao lâu thì dò lại một POI. Áp cho CẢ kết quả rỗng: Commons liên tục có
    # ảnh mới nên "hôm nay không có" không phải kết luận vĩnh viễn.
    photo_cache_days: int = 30
    osm_bbox: str = "10.70,106.60,10.90,106.82"
    osm_max_pois: int = 3_000
    allowed_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:3001,http://127.0.0.1:3001,"
        "http://localhost:8081,http://127.0.0.1:8081"
    )

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def osm_bbox_values(self) -> tuple[float, float, float, float]:
        values = tuple(float(value.strip()) for value in self.osm_bbox.split(","))
        if len(values) != 4:
            raise ValueError("OSM_BBOX must be south,west,north,east")
        south, west, north, east = values
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            raise ValueError("OSM_BBOX is invalid")
        return south, west, north, east

    @model_validator(mode="after")
    def reject_development_credentials_in_production(self) -> "Settings":
        if self.app_env != "production":
            return self
        lowered_url = self.database_url.lower()
        unsafe_fragments = ("nearby:nearby@", "nearby_dev_only", "nearby_test_only")
        if any(fragment in lowered_url for fragment in unsafe_fragments):
            raise ValueError("Production DATABASE_URL must not use development/test credentials")
        if not self.cors_origins or "*" in self.cors_origins:
            raise ValueError("Production ALLOWED_ORIGINS must contain explicit trusted origins")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
