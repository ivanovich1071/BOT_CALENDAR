# Один образ для API и бота — команды разные, см. docker-compose.prod.yml
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости отдельным слоем: при правке кода пересборка не качает их заново
COPY requirements.lock.txt .
RUN pip install -r requirements.lock.txt

COPY alembic.ini .
COPY app ./app
COPY admin ./admin
COPY scripts ./scripts
# Шаблоны «пакета компании» — для python -m app.cli import-company seed/...
COPY seed ./seed

RUN useradd --system --uid 10001 --no-create-home booking && chown -R booking /app
USER booking

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
