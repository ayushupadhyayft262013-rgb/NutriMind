FROM python:3.11-slim

WORKDIR /app

# Install curl for webhook registration
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Make entrypoint executable and fix line endings (Windows CRLF -> Unix LF)
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

# Expose ports (8000 for HTTP, 8443 for HTTPS)
EXPOSE 8000 8443

# Use entrypoint script
CMD ["./entrypoint.sh"]
