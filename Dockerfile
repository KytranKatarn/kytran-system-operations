FROM python:3.12-slim

WORKDIR /app

# Install system deps for psutil
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --no-create-home --shell /bin/false appuser

COPY pyproject.toml README.md LICENSE ./
COPY kytran_server_manager/ ./kytran_server_manager/

RUN pip install --no-cache-dir .

# Set permissions before switching user
RUN mkdir -p /data && chown appuser:appuser /data \
    && chown -R appuser:appuser /usr/local/lib/python3.12/site-packages/kytran_server_manager/

USER appuser

ENV KSM_HOST=0.0.0.0
ENV KSM_PORT=8085
ENV KSM_DATA_DIR=/data

EXPOSE 8085

VOLUME ["/data"]

CMD ["kytran-system-operations"]
