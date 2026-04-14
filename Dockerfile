FROM python:3.12-slim

WORKDIR /app

# Install system deps for psutil
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with docker group access (GID must match host's docker socket)
ARG DOCKER_GID=988
RUN groupadd -g ${DOCKER_GID} docker || true \
    && useradd --no-create-home --shell /bin/false -G docker appuser

COPY pyproject.toml README.md LICENSE ./
COPY kytran_system_operations/ ./kytran_system_operations/

RUN pip install --no-cache-dir .

# Set permissions before switching user
RUN mkdir -p /data && chown appuser:appuser /data \
    && chown -R appuser:appuser /usr/local/lib/python3.12/site-packages/kytran_system_operations/

USER appuser

ENV KSO_HOST=0.0.0.0
ENV KSO_PORT=8085
ENV KSO_DATA_DIR=/data

EXPOSE 8085

VOLUME ["/data"]

CMD ["kytran-system-operations"]
