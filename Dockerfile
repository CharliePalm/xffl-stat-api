FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so this layer only invalidates when
# requirements.txt actually changes, not on every source edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


COPY app ./app
COPY shared ./shared
COPY scrapers ./scrapers
# COPY scrapers ./scrapers
# COPY scripts ./scripts

RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6969"]
