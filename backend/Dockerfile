# ===== SMS backend API (Django + django-ninja) =====
# Chỉ chạy phần API. Worker điều khiển MESClient phải chạy trực tiếp trên Windows (cần desktop + WPS),
# không chạy được trong container — xem README.

# ---- Bước 1: build wheel (mysqlclient cần trình biên dịch) ----
FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc pkg-config default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- Bước 2: image chạy thật, gọn nhẹ ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Ho_Chi_Minh
RUN apt-get update && apt-get install -y --no-install-recommends libmariadb3 tzdata curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY --chown=app:app . .
RUN DJANGO_SECRET_KEY=build-only DB_ENGINE=sqlite python manage.py collectstatic --noinput \
    && mkdir -p storage logs && chown -R app:app storage logs staticfiles \
    && chmod +x scripts/entrypoint.sh

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1
ENTRYPOINT ["sh", "scripts/entrypoint.sh"]
