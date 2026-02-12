FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Expose ports (8000 for HTTP, 8443 for HTTPS)
EXPOSE 8000 8443

# Start script: use SSL if certs exist, otherwise plain HTTP
CMD ["sh", "-c", "\
    if [ -f /app/certs/cert.pem ] && [ -f /app/certs/key.pem ]; then \
    echo '🔒 Starting with HTTPS on port 8443'; \
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8443 \
    --ssl-certfile /app/certs/cert.pem --ssl-keyfile /app/certs/key.pem; \
    else \
    echo '🔓 Starting with HTTP on port 8000'; \
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8000; \
    fi"]
