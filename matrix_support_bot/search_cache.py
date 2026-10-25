"""Bounded, in-memory single-flight cache for immutable retrieval snapshots.

Only retrieval results are cached, never generated replies or conversation state.
Callers receive independent copies. Keys contain no plaintext user questions.
"""
from collections import OrderedDict
from copy import deepcopy
import threading
import time


class SearchCache:
    def __init__(self, capacity=64, ttl=60., *, clock=time.monotonic):
        if capacity < 1 or ttl <= 0:
            raise ValueError("Positive retrieval cache bounds required")
        self.capacity, self.ttl, self.clock = capacity, ttl, clock
        self.values, self.inflight = OrderedDict(), {}
        self.lock = threading.Lock()
        self.hits = self.misses = self.waits = 0

    def get(self, key, compute):
        while True:
            with self.lock:
                value = self.values.get(key)
                if value is not None and self.clock() < value[0]:
                    self.values.move_to_end(key)
                    self.hits += 1
                    return deepcopy(value[1])
                self.values.pop(key, None)
                waiting = self.inflight.get(key)
                if waiting is None:
                    waiting = self.inflight[key] = threading.Event()
                    self.misses += 1
                    break
                self.waits += 1
            waiting.wait()
        try:
            result = compute()
            snapshot = deepcopy(result)
            with self.lock:
                self.values[key] = (self.clock() + self.ttl, snapshot)
                self.values.move_to_end(key)
                while len(self.values) > self.capacity:
                    self.values.popitem(last=False)
            return result
        finally:
            # Exceptions are never cached, and never strand another worker.
            with self.lock:
                self.inflight.pop(key).set()

    def metrics(self):
        with self.lock:
            return dict(hits=self.hits, misses=self.misses, waits=self.waits,
                        entries=len(self.values), inflight=len(self.inflight))
