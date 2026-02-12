#!/bin/sh
set -e

# Determine SSL mode
if [ -f /app/certs/cert.pem ] && [ -f /app/certs/key.pem ]; then
    PORT=8443
    SSL_ARGS="--ssl-certfile /app/certs/cert.pem --ssl-keyfile /app/certs/key.pem"
    echo "Starting NutriMind with HTTPS on port $PORT"
else
    PORT=8000
    SSL_ARGS=""
    echo "Starting NutriMind with HTTP on port $PORT"
fi

# Start uvicorn in background
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT $SSL_ARGS &
UVICORN_PID=$!

# Wait for server to be ready
echo "Waiting for server to start..."
for i in $(seq 1 30); do
    if curl -sk https://localhost:$PORT/health > /dev/null 2>&1 || curl -s http://localhost:$PORT/health > /dev/null 2>&1; then
        echo "Server is ready!"
        break
    fi
    sleep 1
done

# Register webhook with Telegram (using curl which handles cert upload reliably)
if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$WEBHOOK_BASE_URL" ] && [ -f /app/certs/cert.pem ]; then
    echo "Registering Telegram webhook with certificate..."
    curl -s -F "url=${WEBHOOK_BASE_URL}/webhook/telegram" \
         -F "certificate=@/app/certs/cert.pem" \
         -F 'allowed_updates=["message"]' \
         "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook"
    echo ""
    echo "Webhook registered!"
fi

# Wait for uvicorn process
wait $UVICORN_PID
