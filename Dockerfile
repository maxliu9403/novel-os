FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY core ./core
COPY agents ./agents
COPY templates ./templates
COPY docker-entrypoint.sh /usr/local/bin/novel-os-entrypoint
RUN chmod +x /usr/local/bin/novel-os-entrypoint

EXPOSE 8000

ENTRYPOINT ["novel-os-entrypoint"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
