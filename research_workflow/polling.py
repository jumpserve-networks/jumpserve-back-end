"""Bounded polling delays; this module never retries a transport or admits work."""
import time


class Backoff:
    def __init__(self, initial, maximum):
        if not 0 < initial <= maximum:
            raise ValueError('Polling delay must have a positive bound.')
        self.initial, self.maximum = initial, maximum
        self.reset()

    def reset(self):
        self.delay = self.initial

    def next_delay(self):
        delay = self.delay
        self.delay = min(self.maximum, delay * 2)
        return delay


def wait(delay, deadline):
    """A status read cannot extend its original caller's elapsed-time budget."""
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(min(delay, remaining))
