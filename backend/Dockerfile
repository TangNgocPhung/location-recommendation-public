FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# libgomp1: runtime OpenMP ma LightGBM lien ket dong. Anh python:slim khong co
# san, va thieu no thi `import lightgbm` chet voi "libgomp.so.1: cannot open
# shared object file" — loi xay ra luc NAP MO HINH chu khong phai luc build.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY scripts ./scripts

FROM base AS test

COPY requirements-dev.txt ./requirements-dev.txt
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pytest.ini ./pytest.ini
COPY tests ./tests

CMD ["pytest", "tests/unit", "--cov=app", "--cov-report=term-missing", "--cov-fail-under=20"]

FROM base AS runtime

EXPOSE 8000

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
