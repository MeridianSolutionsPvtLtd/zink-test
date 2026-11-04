# Use Python 3.11 slim image
FROM python:3.11-slim

WORKDIR /app

# System deps (pyodbc / builds)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    unixodbc-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + frontend build copied into public/ by CI before docker build
COPY . .

# Non-secret defaults only — secrets from App Service Application settings
ENV DATABASE_BACKEND=blob
ENV FABRIC_WORKSPACE_NAME=test1
ENV WS_REVENUE_ID=2519f7d8-058d-4795-8d70-e29b045f92eb
ENV MODEL_REVENUE_ID=bca8f683-c1a6-4842-b877-3282cdcf391a

# Azure App Service sets WEBSITES_PORT / PORT — must match uvicorn listen port
ENV PORT=80
ENV WEBSITES_PORT=80
EXPOSE 80

# Shell form so $PORT from App Settings is respected (never hardcode 5001 in Azure)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-80}
