# Build from the repo root so this image can copy `shared/` alongside
# `ui/`:  docker build -f ui/Dockerfile -t xffl-ui .
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY ui/requirements.txt ./ui/requirements.txt
RUN pip install --no-cache-dir -r ui/requirements.txt

COPY ui ./ui
COPY shared ./shared

RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

WORKDIR /app/ui

CMD ["gunicorn", "--bind", "0.0.0.0:6968", "app:app"]
