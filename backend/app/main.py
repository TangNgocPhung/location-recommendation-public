# Entrypoint tương thích ngược cho `uvicorn app.main:app`. Toàn bộ ứng dụng
# thật (routes, middleware) nằm ở `app.api`, nơi chứa đầy đủ tầng ingestion
# và ranking engine.
from .api import app as app
