ARG BASE_IMAGE=ubuntu:24.04@sha256:78d4744ead1f5b19b04d0df558ab182a5300903900755e515b2ca4d10eb2044d
FROM ${BASE_IMAGE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl xz-utils git python3 python3.12 python3.12-venv build-essential \
 && rm -rf /var/lib/apt/lists/*
RUN curl --fail --silent --show-error https://nodejs.org/dist/v24.16.0/node-v24.16.0-linux-x64.tar.xz -o /tmp/node.tar.xz \
 && echo 'd804845d34eddc21dc1092b519d643ef40b1f58ec5dec5c22b1f4bd8fabde6c9  /tmp/node.tar.xz' | sha256sum --check \
 && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
 && rm /tmp/node.tar.xz \
 && python3.12 -m venv /opt/jumpserve-tools \
 && /opt/jumpserve-tools/bin/python -m pip install --no-cache-dir pydantic==2.13.5 boto3==1.42.4 \
 && /opt/jumpserve-tools/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12)' \
 && /usr/bin/python3 -c 'import sys; assert sys.version_info[:2] == (3, 12)'
ENV PATH="/opt/jumpserve-tools/bin:$PATH"
ENV HOME=/tmp
WORKDIR /workspace
