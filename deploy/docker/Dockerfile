# Dockerfile for YouTube Shorts Agent with Embedded Router & Local OmniRoute
FROM python:3.11-slim

# Install system dependencies (ffmpeg, curl, git, build essentials)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    libfontconfig1 \
    libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications first for Docker layer caching
COPY requirements.txt requirements-dev.txt* /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source code
COPY . /app

# Expose default OmniRoute port if local proxy is started
EXPOSE 20128

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Default command: execute pipeline dry-run or embedded router health check
CMD ["python", "scripts/generate_phase2.py", "--batch", "1", "--dry-run"]
