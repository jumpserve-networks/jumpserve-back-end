FROM jumpserve-kernel-metrics-test@sha256:0c78738d778fc28dd126f3a3f9f0fb5c8c2053ca568f7d01270f4a3e26715d40
RUN DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends iperf3 iproute2 iputils-ping ethtool
