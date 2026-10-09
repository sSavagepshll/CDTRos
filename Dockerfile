FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app app.py .
COPY --chown=app:app entrypoint.py .
COPY --chown=app:app templates ./templates
COPY --chown=app:app static ./static
RUN mkdir -p /app/uploads && chown app:app /app/uploads

USER app
EXPOSE 8000

CMD ["python", "entrypoint.py"]
