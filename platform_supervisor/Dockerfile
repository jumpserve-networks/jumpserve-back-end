ARG BASE_IMAGE=ubuntu@sha256:e0f80c829509ef4df9e76bd8944eb7b8ec25cbb379b112a7f9448b2c77a77c9b
FROM ${BASE_IMAGE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl xz-utils git python3 python3-pip build-essential \
 && rm -rf /var/lib/apt/lists/*
RUN curl --fail --silent --show-error https://nodejs.org/dist/v24.16.0/node-v24.16.0-linux-x64.tar.xz -o /tmp/node.tar.xz \
 && echo 'd804845d34eddc21dc1092b519d643ef40b1f58ec5dec5c22b1f4bd8fabde6c9  /tmp/node.tar.xz' | sha256sum --check \
 && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
 && rm /tmp/node.tar.xz \
 && python3 -m pip install --no-cache-dir pydantic==2.13.5 boto3==1.42.4
ENV HOME=/tmp
WORKDIR /workspace
