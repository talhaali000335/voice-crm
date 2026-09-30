FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EMBEDDING_CACHE_DIR=/opt/models

WORKDIR /app

# 1) Dependencies (cached until requirements.txt changes)
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) Bake the embedding model into the image so containers start fast and offline
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/models')" \
    && chmod -R a+rX /opt/models

# 3) App code. Files stay root-owned and read-only for the app user
#    (no chown -R, which would duplicate every file in a new image layer).
COPY . .
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod 755 /app/entrypoint.sh \
    && DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
# One worker + threads: the embedding model is loaded once and Prometheus counters stay consistent.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "1", "--threads", "8", "--timeout", "55", \
     "--worker-tmp-dir", "/dev/shm", "--access-logfile", "-"]
