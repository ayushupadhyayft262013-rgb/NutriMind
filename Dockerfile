FROM python:3.11-slim

WORKDIR /app

# Install curl for webhook registration
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Add non-root user
RUN useradd -m -U -s /bin/bash nutrimind

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Make entrypoint executable, fix line endings, and set ownership
RUN sed -i 's/\r$//' entrypoint.sh \
    && chmod +x entrypoint.sh \
    && chown -R nutrimind:nutrimind /app \
    && mkdir -p /app/data \
    && chown -R nutrimind:nutrimind /app/data

# Switch to non-root user
USER nutrimind

# Expose ports (8000 for HTTP, 8443 for HTTPS)
EXPOSE 8000 8443

# Use entrypoint script
CMD ["./entrypoint.sh"]
