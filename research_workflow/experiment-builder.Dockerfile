# Build with a verified Ubuntu 22.04 BASE_IMAGE@sha256 digest, then retain the
# resulting platform-specific digest and original build log before admission.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG WORKSPACE_UID=1000
ARG WORKSPACE_GID=1000
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 python3-venv python3-pip build-essential pkg-config sudo \
    && dpkg-query -W > /jumpserve-image-packages.txt && rm -rf /var/lib/apt/lists/*
RUN test "${WORKSPACE_UID}" -gt 0 && test "${WORKSPACE_GID}" -gt 0 \
    && (getent group "${WORKSPACE_GID}" || groupadd --gid "${WORKSPACE_GID}" jumpserve) \
    && useradd --no-log-init --uid "${WORKSPACE_UID}" --gid "${WORKSPACE_GID}" --create-home --shell /bin/bash jumpserve \
    && printf 'jumpserve ALL=(ALL) NOPASSWD: ALL\nDefaults:jumpserve !requiretty\n' > /etc/sudoers.d/jumpserve \
    && chmod 440 /etc/sudoers.d/jumpserve \
    && visudo -cf /etc/sudoers.d/jumpserve
WORKDIR /workspace
