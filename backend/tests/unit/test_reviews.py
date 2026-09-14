from __future__ import annotations

from datetime import datetime, timezone

from app import reviews


class FakeCursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.executed: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, query, values):
        self.executed.append((str(query), values.copy()))

    def fetchone(self):
        return next(self.rows)


class FakeConnection:
    def __init__(self, rows):
        self.cursor_instance = FakeCursor(rows)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True


def test_submit_review_inserts_first_review(monkeypatch):
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    connection = FakeConnection(
        [
            {"exists": 1},
            None,
            {
                "id": "review-1",
                "author_name": "Minh Anh",
                "rating": 5,
                "title": "Rất thích",
                "body": "Không gian đẹp",
                "created_at": now,
                "updated_at": now,
            },
            {"rating": 5.0, "review_count": 1},
        ]
    )
    monkeypatch.setattr(reviews.psycopg, "connect", lambda *_args, **_kwargs: connection)

    result = reviews.submit_review(
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        5,
        author_name="  Minh Anh  ",
        title="  Rất thích  ",
        body="  Không gian đẹp  ",
        database_url="postgresql://test",
    )

    assert result == {
        "reviewId": "review-1",
        "createdAt": now.isoformat(),
        "updatedAt": now.isoformat(),
        "updated": False,
        "ratingMean": 5.0,
        "ratingCount": 1,
    }
    assert "INSERT INTO poi_reviews" in connection.cursor_instance.executed[2][0]
    assert connection.cursor_instance.executed[2][1]["author_name"] == "Minh Anh"
    assert connection.committed


def test_submit_review_updates_existing_review(monkeypatch):
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    connection = FakeConnection(
        [
            {"exists": 1},
            {"id": "review-1"},
            {
                "id": "review-1",
                "author_name": None,
                "rating": 3,
                "title": None,
                "body": "Ổn",
                "created_at": now,
                "updated_at": now,
            },
            {"rating": 4.2, "review_count": 8},
        ]
    )
    monkeypatch.setattr(reviews.psycopg, "connect", lambda *_args, **_kwargs: connection)

    result = reviews.submit_review(
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        3,
        body="Ổn",
        database_url="postgresql://test",
    )

    assert result is not None and result["updated"] is True
    assert result["ratingCount"] == 8
    assert "UPDATE poi_reviews SET" in connection.cursor_instance.executed[2][0]
    assert connection.cursor_instance.executed[2][1]["review_id"] == "review-1"


def test_get_user_review_returns_editable_fields(monkeypatch):
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    connection = FakeConnection(
        [
            {
                "id": "review-1",
                "author_name": "Lan",
                "rating": 4,
                "title": "Đáng thử",
                "body": "Phục vụ nhanh",
                "created_at": now,
                "updated_at": now,
            }
        ]
    )
    monkeypatch.setattr(reviews.psycopg, "connect", lambda *_args, **_kwargs: connection)

    result = reviews.get_user_review("poi", "session", database_url="postgresql://test")

    assert result == {
        "id": "review-1",
        "authorName": "Lan",
        "rating": 4,
        "title": "Đáng thử",
        "body": "Phục vụ nhanh",
        "createdAt": now.isoformat(),
        "updatedAt": now.isoformat(),
    }
