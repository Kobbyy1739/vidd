FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
# One worker: job state lives in memory. Threads handle concurrency.
CMD gunicorn -w 1 --threads 8 --timeout 0 -b 0.0.0.0:${PORT} app:app
