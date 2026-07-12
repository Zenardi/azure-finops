# syntax=docker/dockerfile:1
# Build stage: install deps into a venv.
# Pinned to Python 3.13 (the tested/locked dependency set ships 3.13 wheels). The
# previous Chainguard `latest-dev` base used a moving tag that drifted to a newer
# Python without wheels for our pins, so the venv build failed. Pinning the minor
# version keeps builds reproducible while still receiving patch/security updates.
FROM python:3.13-slim-trixie AS build
WORKDIR /app
COPY requirements.txt .
RUN python -m venv /venv \
 && /venv/bin/pip install --no-cache-dir --upgrade pip \
 && /venv/bin/pip install --no-cache-dir -r requirements.txt

# Runtime stage: same pinned base, minimal, run as a nonroot user (uid 65532 to
# match the appdata volume perms wired in docker-compose.yml).
FROM python:3.13-slim-trixie
WORKDIR /app
ENV PATH="/venv/bin:$PATH" \
    HOME=/home/nonroot \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_DATA_DIR=/data
COPY --from=build /venv /venv
COPY azure_finops ./azure_finops
# Nonroot user + a writable data dir for the appdata named volume.
RUN useradd -u 65532 -m -d /home/nonroot -s /usr/sbin/nologin nonroot \
 && mkdir -p /data \
 && chown -R 65532:65532 /data /app
USER 65532:65532
ENTRYPOINT ["python", "-m", "azure_finops.cli"]
CMD ["api"]
