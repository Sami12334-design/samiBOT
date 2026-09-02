# Optional stronger deployment: bot + local bgutil PO-token provider in one container.
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN git clone --depth 1 --branch 1.3.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil \
    && cd /opt/bgutil/server && npm ci --omit=dev --no-audit --no-fund && npm ci --no-audit --no-fund && npx tsc
COPY bot.py .
COPY start.sh .
RUN chmod +x start.sh
EXPOSE 10000
CMD ["/app/start.sh"]
