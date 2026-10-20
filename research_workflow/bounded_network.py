"""Immutable host-local TCP experiment. No scripts, URLs, AWS APIs or files.

Deployed alone in a 128-MB/5-second Lambda version. The outer timeout is the
independent watchdog; this process also enforces a shorter monotonic deadline.
"""
import hashlib
import json
import socket
import threading
import time

VERSION = 'bounded-tcp-v1'
METRICS = {'round_trip_time_ms': 'ms', 'received_bytes': 'bytes'}


def validate(config):
    if not isinstance(config, dict) or set(config) != {'payload_bytes', 'exchanges', 'application_delay_ms'}:
        raise ValueError('The TCP runner accepts only payload_bytes, exchanges and application_delay_ms.')
    for key, low, high in [('payload_bytes', 1, 65536), ('exchanges', 1, 16), ('application_delay_ms', 0, 20)]:
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError('TCP configuration exceeds fixed resource bounds: ' + key)
    return config


def run(config):
    validate(config)
    began = time.monotonic()
    deadline = began + 3
    payload = b'J' * config['payload_bytes']
    received = []
    errors = []
    sockets = []

    def timeout(stream):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('TCP experiment deadline reached.')
        stream.settimeout(min(remaining, .5))

    def read(stream):
        result = bytearray()
        while len(result) < len(payload):
            timeout(stream)
            data = stream.recv(min(8192, len(payload) - len(result)))
            if not data:
                raise ValueError('Incomplete TCP payload; no zero was imputed.')
            result.extend(data)
        if bytes(result) != payload:
            raise ValueError('TCP payload integrity differs.')
        return len(result)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sockets.append(listener)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)

    def serve():
        try:
            timeout(listener)
            stream, _ = listener.accept()
            sockets.append(stream)
            with stream:
                stream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                for _ in range(config['exchanges']):
                    read(stream)
                    time.sleep(config['application_delay_ms'] / 1000)
                    timeout(stream)
                    stream.sendall(payload)
        except Exception as error:
            errors.append(type(error).__name__)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    failure = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            sockets.append(client)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            timeout(client)
            client.connect(listener.getsockname())
            for index in range(config['exchanges']):
                tick = time.monotonic_ns()
                timeout(client)
                client.sendall(payload)
                size = read(client)
                elapsed_ms = (time.monotonic_ns() - tick) / 1000000
                for metric, value in [('round_trip_time_ms', elapsed_ms), ('received_bytes', size)]:
                    received.append(dict(observation_key=str(index), metric=metric, units=METRICS[metric], value=value,
                                         status='recorded', reason=None, metadata={'exchange_index': index}))
        thread.join(timeout=max(0, deadline - time.monotonic()))
        if thread.is_alive() or errors:
            raise ValueError('TCP exchange did not finish cleanly.')
    except Exception as error:
        failure = 'TCP experiment failed (' + type(error).__name__ + '); completed exchanges are retained and missing exchanges remain missing.'
    finally:
        for stream in sockets:
            stream.close()
        thread.join(timeout=.6)
    return dict(version='network-observations-v1', observations=received,
                provenance=dict(runner=VERSION, data_origin='new-measurement', environment='host-local-emulated',
                                transport='TCP over 127.0.0.1; controlled application delay, not kernel netem or Internet RTT',
                                payload_sha256=hashlib.sha256(payload).hexdigest(), cleanup='All client/server/listener sockets closed.',
                                failure=failure,
                                independence='Exchanges share process/host/connection; no independent-replication claim or inferential interval.'),
                usage=dict(wall_seconds=time.monotonic() - began, application_payload_bytes=2 * len(payload) * config['exchanges'],
                           remote_network_bytes=0, experiment_instances=0, measured_charges_usd=None))


def handler(event, context=None):
    if not isinstance(event, dict) or set(event) != {'version', 'config'} or event['version'] != VERSION:
        raise ValueError('Exact bounded TCP invocation required.')
    result = run(event['config'])
    if len(json.dumps(result, separators=(',', ':')).encode()) > 32000:
        raise ValueError('Bounded TCP output exceeds its transport limit.')
    return result
