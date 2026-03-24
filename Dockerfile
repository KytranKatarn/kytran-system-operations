FROM python:3.11-slim

WORKDIR /app

# Install system deps for psutil
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY kytran_server_manager/ ./kytran_server_manager/

RUN pip install --no-cache-dir .

ENV KSM_HOST=0.0.0.0
ENV KSM_PORT=8080
ENV KSM_DATA_DIR=/data

EXPOSE 8080

VOLUME ["/data"]

CMD ["kytran-server-manager"]
