# =============================================================================
# Federal Funds Matchmaker — single image, two roles
#
# Roles are controlled by the MODE environment variable (set in Container Apps):
#   MODE=app   Start the Streamlit web UI (default)
#   MODE=job   Run the ingest-normalize-score-summarize pipeline once and exit
#
# Build:
#   az acr build --registry $ACR --image ffm:v1 .
#
# Run locally (web):
#   docker run -p 8000:8000 \
#     -e MODE=app \
#     -e STORAGE_CONN="..." \
#     -e AOAI_ENDPOINT="..." \
#     -e AOAI_KEY="..." \
#     ffm:v1
#
# Run locally (job, offline):
#   docker run -e MODE=job -e NSF_OFFLINE=true -e STORAGE_CONN="..." ffm:v1
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — Python base with system dependencies
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS base

# Keeps Python from buffering stdout/stderr (important for Container Apps logs)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System packages needed by:
#   lxml        — fast HTML parser for BeautifulSoup (NSF pages can be 500 KB+)
#   libxml2     — lxml dependency
#   libxslt1.1  — lxml dependency
#   curl        — smoke-testing inside the container
#   ca-certificates — HTTPS to Grants.gov, NSF, Azure
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Stage 2 — Python dependencies (cached, rebuilt only when requirements change)
# -----------------------------------------------------------------------------
FROM base AS deps

COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 3 — Optional Playwright layer
#
# Playwright adds ~350 MB (Chromium + libs). Only needed if NSF serves
# JS-rendered HTML that httpx+BeautifulSoup cannot parse.
#
# Build WITHOUT Playwright (default, smaller image):
#   az acr build --registry $ACR --image ffm:v1 .
#
# Build WITH Playwright:
#   az acr build --registry $ACR --image ffm:v1 \
#     --build-arg INSTALL_PLAYWRIGHT=true .
# -----------------------------------------------------------------------------
ARG INSTALL_PLAYWRIGHT=false

FROM deps AS playwright_layer
RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
        pip install playwright==1.44.0 \
        && playwright install chromium \
        && playwright install-deps chromium; \
    fi

# -----------------------------------------------------------------------------
# Stage 4 — Application code
# -----------------------------------------------------------------------------
FROM playwright_layer AS app

COPY . .

# Ensure the offline fixture directory always exists
RUN mkdir -p scrapers/fixtures

# Non-root user (Azure Container Apps security best practice)
RUN useradd --create-home --shell /bin/bash ffmuser \
    && chown -R ffmuser:ffmuser /app
USER ffmuser

# Default web UI port; match --target-port in the az containerapp create command
EXPOSE 8000

# Expose whether Playwright was installed so scrapers can detect it at runtime
ENV PLAYWRIGHT_AVAILABLE=${INSTALL_PLAYWRIGHT}

# -----------------------------------------------------------------------------
# Entrypoint — routes to the right role based on MODE env var
# -----------------------------------------------------------------------------
CMD ["sh", "-c", "\
  if [ \"$MODE\" = 'job' ]; then \
    echo 'Starting pipeline job...'; \
    python -m job; \
  else \
    echo 'Starting web app...'; \
    streamlit run app.py \
      --server.port 8000 \
      --server.address 0.0.0.0 \
      --server.headless true \
      --server.enableCORS false \
      --server.enableXsrfProtection false; \
  fi"]
