FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable bufferless logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies (including ffmpeg for voice audio processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create directories for persistent volumes
RUN mkdir -p /app/data /app/credentials

# Copy application source code and template
COPY app/ /app/app/
COPY ["To-Do template.md", "/app/"]

CMD ["python", "-m", "app.main"]
