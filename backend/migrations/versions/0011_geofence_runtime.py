"""Phần còn thiếu để geofence chạy được: liên kết tới POI và bảng ghi nhận lượt kích hoạt.

Revision ID: 0011_geofence_runtime
Revises: 0010_reparse_opening_hours

Migration 0003 đã thiết kế `geofence_subscriptions` rất kỹ — center GEOGRAPHY,
radius_meters, category_filters, tag_filters, minimum_rating,
notify_only_when_open, expires_at, GIST index và partial index. Nhưng suốt từ
đó tới nay grep `geofence` trong `backend/**/*.py` (trừ migrations) trả về 0
kết quả: không endpoint, không worker, không gì đọc bảng này.

Khi nối dây thật thì lộ ra hai thứ còn thiếu trong schema:

**`poi_id` + `label`.** Bảng cũ chỉ mô tả một hình tròn trên bản đồ. Nhưng thao
tác người dùng thật là "nhắc tôi khi tới gần *quán này*", và thông báo bật lên
phải nói được nó nói về địa điểm nào. Không có hai cột này thì tầng ứng dụng
buộc phải tự đoán POI nào gần tâm nhất — vừa sai vừa tốn một truy vấn.

**`geofence_hits`.** Không ghi lại lượt kích hoạt thì không có cách nào chống
báo trùng: người dùng đứng yên trong vùng, cứ 20 giây một ping, là 20 giây một
thông báo. Cột `notified_at` tách "đã phát hiện" khỏi "đã báo cho người dùng" —
hai việc khác nhau, và trộn chúng lại thì một lần mất kết nối SSE sẽ nuốt luôn
thông báo mà không ai biết.
"""

from alembic import op

revision = "0011_geofence_runtime"
down_revision = "0010_reparse_opening_hours"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE geofence_subscriptions
            ADD COLUMN IF NOT EXISTS poi_id UUID REFERENCES pois(id) ON DELETE CASCADE,
            ADD COLUMN IF NOT EXISTS label TEXT,
            ADD COLUMN IF NOT EXISTS cooldown_minutes INTEGER NOT NULL DEFAULT 30
                CHECK (cooldown_minutes BETWEEN 1 AND 1440)
        """
    )
    # Một phiên chỉ đăng ký một lần cho mỗi POI. Không có ràng buộc này thì mỗi
    # lần bấm nút lại sinh thêm một vùng chồng lên vùng cũ, và người dùng nhận
    # đúng n thông báo giống hệt nhau cho n lần bấm.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS geofence_subscriptions_session_poi_idx
            ON geofence_subscriptions (session_id, poi_id)
            WHERE poi_id IS NOT NULL AND session_id IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS geofence_hits (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            subscription_id UUID NOT NULL
                REFERENCES geofence_subscriptions(id) ON DELETE CASCADE,
            session_id UUID,
            event_id UUID,
            poi_id UUID,
            distance_meters DOUBLE PRECISION NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            notified_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS geofence_hits_subscription_time_idx "
        "ON geofence_hits (subscription_id, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS geofence_hits_session_time_idx "
        "ON geofence_hits (session_id, occurred_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS geofence_hits")
    op.execute("DROP INDEX IF EXISTS geofence_subscriptions_session_poi_idx")
    op.execute(
        """
        ALTER TABLE geofence_subscriptions
            DROP COLUMN IF EXISTS cooldown_minutes,
            DROP COLUMN IF EXISTS label,
            DROP COLUMN IF EXISTS poi_id
        """
    )
