"""Seed deterministic POIs and the Vietnamese gazetteer used by the demo.

Revision ID: 0002_seed_demo_data
Revises: 0001_initial_schema
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_seed_demo_data"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


POIS = [
    ("10000000-0000-0000-0000-000000000001", "Cà phê Bến Nghé", "Cà phê rang xay, không gian yên tĩnh để làm việc.", "cafe", "Cà phê", "22 Lý Tự Trọng, Quận 1", 106.7018, 10.7784, 4.7, 286, 0.91),
    ("10000000-0000-0000-0000-000000000002", "Phở Nhà Mình", "Phở bò truyền thống, phục vụ từ sáng sớm.", "restaurant", "Ăn uống", "38 Pasteur, Quận 1", 106.6996, 10.7748, 4.6, 412, 0.95),
    ("10000000-0000-0000-0000-000000000003", "Bảo tàng Thành phố", "Không gian lịch sử và kiến trúc giữa trung tâm Sài Gòn.", "museum", "Văn hóa", "65 Lý Tự Trọng, Quận 1", 106.6994, 10.7763, 4.5, 732, 0.88),
    ("10000000-0000-0000-0000-000000000004", "Vườn xanh Tao Đàn", "Khoảng xanh rộng, phù hợp đi bộ và nghỉ trưa.", "park", "Công viên", "Trương Định, Quận 1", 106.6937, 10.7742, 4.6, 968, 0.90),
    ("10000000-0000-0000-0000-000000000005", "Bếp Chợ Lớn", "Món Việt hiện đại, phù hợp nhóm bạn và gia đình.", "restaurant", "Ăn uống", "112 Nguyễn Huệ, Quận 1", 106.7045, 10.7735, 4.4, 197, 0.79),
    ("10000000-0000-0000-0000-000000000006", "The Reading Room", "Hiệu sách nhỏ kết hợp cà phê và khu đọc tại chỗ.", "bookstore", "Mua sắm", "14 Đồng Khởi, Quận 1", 106.7059, 10.7769, 4.8, 154, 0.86),
    ("10000000-0000-0000-0000-000000000007", "Nhà hát Thành phố", "Công trình kiến trúc và điểm biểu diễn nghệ thuật nổi bật.", "theatre", "Văn hóa", "7 Công trường Lam Sơn, Quận 1", 106.7033, 10.7765, 4.7, 1205, 0.94),
    ("10000000-0000-0000-0000-000000000008", "Bánh mì Góc Phố", "Bánh mì nóng, phục vụ nhanh và có lựa chọn chay.", "restaurant", "Ăn uống", "54 Lê Thánh Tôn, Quận 1", 106.7024, 10.7790, 4.3, 341, 0.82),
    ("10000000-0000-0000-0000-000000000009", "Công viên Bến Bạch Đằng", "Không gian ven sông phù hợp dạo bộ vào chiều tối.", "park", "Công viên", "Tôn Đức Thắng, Quận 1", 106.7073, 10.7739, 4.6, 1430, 0.96),
    ("10000000-0000-0000-0000-000000000010", "Cà phê Sân Thượng 81", "Không gian thoáng với góc nhìn trung tâm thành phố.", "cafe", "Cà phê", "81 Nguyễn Du, Quận 1", 106.6963, 10.7794, 4.5, 228, 0.83),
    ("10000000-0000-0000-0000-000000000011", "Nhà sách Trung Tâm", "Sách tiếng Việt, ngoại văn và khu văn phòng phẩm.", "bookstore", "Mua sắm", "40 Nguyễn Huệ, Quận 1", 106.7041, 10.7717, 4.4, 516, 0.85),
    ("10000000-0000-0000-0000-000000000012", "Bún bò Sài Gòn", "Bún bò vị đậm, có phục vụ buổi trưa và buổi tối.", "restaurant", "Ăn uống", "18 Nam Kỳ Khởi Nghĩa, Quận 1", 106.6968, 10.7728, 4.5, 605, 0.89),
    ("10000000-0000-0000-0000-000000000013", "Hồ Con Rùa", "Quảng trường quen thuộc, điểm hẹn buổi tối của giới trẻ Quận 3.", "landmark", "Địa danh", "Công trường Quốc Tế, Quận 3", 106.6910, 10.7838, 4.5, 890, 0.87),
    ("10000000-0000-0000-0000-000000000014", "Landmark 81 Skyview", "Đài quan sát trên cao với tầm nhìn toàn cảnh sông Sài Gòn.", "landmark", "Địa danh", "720A Điện Biên Phủ, Bình Thạnh", 106.7217, 10.7944, 4.7, 2103, 0.97),
    ("10000000-0000-0000-0000-000000000015", "Cà phê Thảo Điền", "Không gian sân vườn rộng rãi, phù hợp cuối tuần cùng gia đình.", "cafe", "Cà phê", "12 Xuân Thủy, Thảo Điền, TP. Thủ Đức", 106.7328, 10.8043, 4.6, 341, 0.84),
    ("10000000-0000-0000-0000-000000000016", "Chợ Bến Thành", "Chợ truyền thống nổi tiếng với đặc sản và quà lưu niệm.", "market", "Chợ", "Lê Lợi, Quận 1", 106.6983, 10.7725, 4.3, 3204, 0.93),
    ("10000000-0000-0000-0000-000000000017", "Chợ Bình Tây", "Chợ đầu mối lớn ở khu Chợ Lớn, kiến trúc cổ đặc trưng.", "market", "Chợ", "57A Tháp Mười, Quận 6", 106.6535, 10.7500, 4.2, 1120, 0.78),
    ("10000000-0000-0000-0000-000000000018", "Bảo tàng Chứng tích Chiến tranh", "Bảo tàng lịch sử chiến tranh, nhiều du khách quốc tế ghé thăm.", "museum", "Văn hóa", "28 Võ Văn Tần, Quận 3", 106.6919, 10.7794, 4.4, 2760, 0.90),
    ("10000000-0000-0000-0000-000000000019", "Nhà thờ Đức Bà", "Nhà thờ chính tòa mang kiến trúc Pháp giữa trung tâm thành phố.", "landmark", "Địa danh", "Công xã Paris, Quận 1", 106.6990, 10.7798, 4.6, 4021, 0.96),
    ("10000000-0000-0000-0000-000000000020", "Dinh Độc Lập", "Di tích lịch sử quốc gia, kiến trúc đặc trưng thập niên 1960.", "landmark", "Địa danh", "135 Nam Kỳ Khởi Nghĩa, Quận 1", 106.6953, 10.7770, 4.5, 3312, 0.92),
    ("10000000-0000-0000-0000-000000000021", "Vincom Center Đồng Khởi", "Trung tâm thương mại cao cấp với nhiều thương hiệu quốc tế.", "shopping_mall", "Mua sắm", "72 Lê Thánh Tôn, Quận 1", 106.7028, 10.7788, 4.4, 1543, 0.88),
    ("10000000-0000-0000-0000-000000000022", "Gym Fit24 Phú Nhuận", "Phòng tập đầy đủ thiết bị, lớp học nhóm buổi tối.", "gym", "Thể thao", "20 Phan Đăng Lưu, Phú Nhuận", 106.6820, 10.7988, 4.3, 210, 0.72),
    ("10000000-0000-0000-0000-000000000023", "Công viên Lê Văn Tám", "Công viên lớn với nhiều cây xanh, phù hợp chạy bộ buổi sáng.", "park", "Công viên", "Hai Bà Trưng, Quận 1", 106.6942, 10.7860, 4.5, 875, 0.86),
    ("10000000-0000-0000-0000-000000000024", "Bún riêu Quận 4", "Quán bún riêu lâu năm, nước dùng đậm đà kiểu miền Nam.", "restaurant", "Ăn uống", "15 Tôn Thất Thuyết, Quận 4", 106.7008, 10.7591, 4.4, 388, 0.80),
    ("10000000-0000-0000-0000-000000000025", "Cơm tấm Quận 5", "Cơm tấm sườn bì chả, phần ăn lớn giá bình dân.", "restaurant", "Ăn uống", "88 Nguyễn Trãi, Quận 5", 106.6689, 10.7548, 4.5, 645, 0.83),
    ("10000000-0000-0000-0000-000000000026", "Nhà sách Phú Nhuận", "Nhà sách khu vực, có góc đọc sách thiếu nhi.", "bookstore", "Mua sắm", "212 Phan Xích Long, Phú Nhuận", 106.6870, 10.7975, 4.2, 96, 0.65),
    ("10000000-0000-0000-0000-000000000027", "Cà phê Ban Công", "Quán cà phê nhỏ nhìn ra phố, nổi tiếng trên mạng xã hội.", "cafe", "Cà phê", "123 Lý Chính Thắng, Quận 3", 106.6870, 10.7876, 4.6, 512, 0.89),
    ("10000000-0000-0000-0000-000000000028", "Nhà hát Kịch Sân khấu nhỏ", "Sân khấu kịch nói quy mô nhỏ, lịch diễn hàng tuần.", "theatre", "Văn hóa", "5B Võ Văn Tần, Quận 3", 106.6903, 10.7810, 4.3, 187, 0.74),
]

ALIASES = [
    ("bến thành", "Chợ Bến Thành", "Lê Lợi, Quận 1", 106.6983, 10.7725, 100),
    ("chợ bến thành", "Chợ Bến Thành", "Lê Lợi, Quận 1", 106.6983, 10.7725, 100),
    ("nhà thờ đức bà", "Nhà thờ Đức Bà Sài Gòn", "Công xã Paris, Quận 1", 106.6990, 10.7798, 100),
    ("dinh độc lập", "Dinh Độc Lập", "135 Nam Kỳ Khởi Nghĩa, Quận 1", 106.6953, 10.7770, 95),
    ("phố đi bộ", "Phố đi bộ Nguyễn Huệ", "Nguyễn Huệ, Quận 1", 106.7030, 10.7740, 95),
    ("nguyễn huệ", "Phố đi bộ Nguyễn Huệ", "Nguyễn Huệ, Quận 1", 106.7030, 10.7740, 90),
    ("quận 1", "Trung tâm Quận 1", "Quận 1, TP. Hồ Chí Minh", 106.7009, 10.7757, 80),
    ("tao đàn", "Công viên Tao Đàn", "Trương Định, Quận 1", 106.6937, 10.7742, 90),
    ("bạch đằng", "Công viên Bến Bạch Đằng", "Tôn Đức Thắng, Quận 1", 106.7073, 10.7739, 90),
]


def upgrade() -> None:
    connection = op.get_bind()
    poi_rows = [
        {
            "id": row[0], "name": row[1], "description": row[2],
            "category": row[3], "category_label": row[4], "address": row[5],
            "longitude": row[6], "latitude": row[7], "rating": row[8],
            "review_count": row[9], "popularity_score": row[10],
        }
        for row in POIS
    ]
    connection.execute(
        sa.text(
            """
            INSERT INTO pois (
                id, name, description, category, category_label, address, location,
                rating, review_count, popularity_score
            ) VALUES (
                CAST(:id AS uuid), :name, :description, :category, :category_label, :address,
                ST_SetSRID(ST_Point(:longitude, :latitude), 4326)::geography,
                :rating, :review_count, :popularity_score
            ) ON CONFLICT (id) DO NOTHING
            """
        ),
        poi_rows,
    )
    alias_rows = [
        {
            "alias": row[0], "canonical_name": row[1], "address": row[2],
            "longitude": row[3], "latitude": row[4], "priority": row[5],
        }
        for row in ALIASES
    ]
    connection.execute(
        sa.text(
            """
            INSERT INTO geo_aliases (
                alias, canonical_name, address, location, priority
            ) VALUES (
                :alias, :canonical_name, :address,
                ST_SetSRID(ST_Point(:longitude, :latitude), 4326)::geography,
                :priority
            ) ON CONFLICT (alias, canonical_name) DO NOTHING
            """
        ),
        alias_rows,
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM geo_aliases "
            "WHERE alias = :alias AND canonical_name = :canonical_name"
        ),
        [{"alias": row[0], "canonical_name": row[1]} for row in ALIASES],
    )
    connection.execute(
        sa.text("DELETE FROM pois WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": "{" + ",".join(row[0] for row in POIS) + "}"},
    )
