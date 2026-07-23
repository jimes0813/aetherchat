# backend/Dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y git docker.io && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# 构建沙箱镜像时可额外步骤
# docker build -t aether-sandbox:latest -f sandbox/Dockerfile .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
