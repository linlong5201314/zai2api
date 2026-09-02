FROM python:3.12-slim

WORKDIR /app

# camoufox runtime deps (Firefox) + Xvfb for virtual display
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb libgtk-3-0 libdbus-glib-1-2 libxt6 libasound2 libx11-xcb1 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
    libgbm1 libxkbcommon0 libnss3 libnspr4 libcups2 \
    libpango-1.0-0 libcairo2 libatk1.0-0 libatk-bridge2.0-0 \
    libdrm2 libxcb-shm0 libxcb1 fonts-liberation \
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
