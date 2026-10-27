# Build with a verified Ubuntu 22.04 BASE_IMAGE@sha256 digest, then retain the
# resulting platform-specific digest and original build log before admission.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 python3-venv python3-pip build-essential pkg-config \
    && dpkg-query -W > /jumpserve-image-packages.txt && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
