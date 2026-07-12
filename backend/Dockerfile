# syntax=docker/dockerfile:1
# Build stage: install deps into a venv using the Chainguard dev image (has pip).
FROM cgr.dev/chainguard/python:latest-dev AS build
WORKDIR /app
COPY requirements.txt .
RUN python -m venv /venv \
 && /venv/bin/pip install --no-cache-dir --upgrade pip \
 && /venv/bin/pip install --no-cache-dir -r requirements.txt

# Runtime stage: minimal Chainguard image, nonroot (uid 65532) by default.
FROM cgr.dev/chainguard/python:latest
WORKDIR /app
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_DATA_DIR=/data
COPY --from=build /venv /venv
COPY azure_finops ./azure_finops
USER 65532:65532
ENTRYPOINT ["python", "-m", "azure_finops.cli"]
CMD ["api"]
