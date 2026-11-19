"""UUIDv7 generation (RFC 9562).

Time-ordered UUIDs keep B-tree indexes compact. Python 3.12 has no ``uuid.uuid7``.
"""

from __future__ import annotations

import os
import time
import uuid

_48_BITS = (1 << 48) - 1
_62_BITS = (1 << 62) - 1


def uuid7() -> uuid.UUID:
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits
    value = (unix_ms & _48_BITS) << 80
    value |= 0x7 << 76  # version 7
    value |= ((rand >> 62) & 0xFFF) << 64  # rand_a (12 bits)
    value |= 0b10 << 62  # RFC 4122 / 9562 variant
    value |= rand & _62_BITS  # rand_b (62 bits)
    return uuid.UUID(int=value)
