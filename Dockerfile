FROM python:3.12-slim

WORKDIR /app

# camoufox runtime deps (Firefox)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gtk3 libdbus-glib-1-2 libxt6 libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m camoufox fetch

COPY app/ ./app/
COPY main.py .

ENV DATA_DIR=/app/data
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["python", "main.py"]
