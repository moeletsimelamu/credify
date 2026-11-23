"""UUIDv7 generation (RFC 9562).

Time-ordered UUIDs keep B-tree indexes compact. Python 3.12 has no ``uuid.uuid7``.

Ids are also strictly increasing within a process (RFC 9562 §6.2, method 1: the 12-bit
``rand_a`` field is a counter within each millisecond). Rows written in one transaction
often share a timestamp, for example the two status changes of a submission, and ordering
by ``(created_at, id)`` must then still give the order they happened in.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_48_BITS = (1 << 48) - 1
_62_BITS = (1 << 62) - 1
_COUNTER_MAX = 0xFFF
# A new millisecond starts the counter at a random value in the lower half, leaving at least
# 2,048 increments before it would overflow into the next millisecond.
_COUNTER_SEED_MAX = 0x7FF

_lock = threading.Lock()
_last_ms = -1
_counter = 0


def _next_timestamp_and_counter() -> tuple[int, int]:
    global _last_ms, _counter
    with _lock:
        unix_ms = time.time_ns() // 1_000_000
        if unix_ms > _last_ms:
            _last_ms = unix_ms
            _counter = int.from_bytes(os.urandom(2), "big") & _COUNTER_SEED_MAX
        else:
            # Same millisecond (or the clock stepped back): keep counting from the last id.
            _counter += 1
            if _counter > _COUNTER_MAX:
                _last_ms += 1
                _counter = 0
        return _last_ms, _counter


def uuid7() -> uuid.UUID:
    unix_ms, counter = _next_timestamp_and_counter()
    value = (unix_ms & _48_BITS) << 80
    value |= 0x7 << 76  # version 7
    value |= counter << 64  # rand_a: monotonic counter (12 bits)
    value |= 0b10 << 62  # RFC 4122 / 9562 variant
    value |= int.from_bytes(os.urandom(8), "big") & _62_BITS  # rand_b (62 random bits)
    return uuid.UUID(int=value)
