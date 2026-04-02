"""
Bloom filter for ATTEST revocation tracking.

Sizing: m_bits = -K * ln(p) / (ln 2)^2,  k = ceil(-log2(p))
For K=10000, p=0.01: m ≈ 95,851 bits (~11.7 KB), k=7

The filter is always serialised at full m_bits width (constant-size bitmap),
so epoch-delta BF_Δ has the same fixed size regardless of how many tags
were inserted — receivers apply a single bitwise-OR.
"""
import math
import hashlib
from typing import Optional


class BloomFilter:
    """
    Fixed-capacity Bloom filter.

    Parameters
    ----------
    K : int   — maximum number of elements (capacity)
    p : float — target false-positive rate (0 < p < 1)
    """

    def __init__(self, K: int = 10_000, p: float = 0.01,
                 _bits: Optional[bytearray] = None):
        self.K = K
        self.p = p
        # m = number of bits
        self.m = math.ceil(-K * math.log(p) / (math.log(2) ** 2))
        # k = number of hash functions
        self.k = math.ceil(-math.log2(p))
        # storage: bytearray of ceil(m/8) bytes
        n_bytes = math.ceil(self.m / 8)
        self._bits = _bits if _bits is not None else bytearray(n_bytes)
        assert len(self._bits) == n_bytes

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def insert(self, tag: bytes) -> None:
        """Insert a 32-byte tag into the filter."""
        for pos in self._positions(tag):
            self._bits[pos >> 3] |= (1 << (pos & 7))

    def query(self, tag: bytes) -> bool:
        """
        Return True if tag is (probably) present, False if definitely absent.
        False → not revoked (safe to proceed).
        True  → revoked (or false positive; reject).
        """
        return all(
            (self._bits[pos >> 3] >> (pos & 7)) & 1
            for pos in self._positions(tag)
        )

    def merge(self, other: "BloomFilter") -> None:
        """
        In-place union: self |= other (bitwise OR).
        Used by WitUpdate to apply BF_Δ to BF_local.
        """
        assert self.m == other.m, "BF dimensions must match"
        for i in range(len(self._bits)):
            self._bits[i] |= other._bits[i]

    # ------------------------------------------------------------------
    # Serialisation (fixed-size bitmap, always full m_bits width)
    # ------------------------------------------------------------------

    def serialize(self) -> bytes:
        """Return the raw bitmap bytes (fixed size = ceil(m/8))."""
        return bytes(self._bits)

    @classmethod
    def deserialize(cls, data: bytes, K: int = 10_000, p: float = 0.01) -> "BloomFilter":
        """Reconstruct a BloomFilter from its serialised bitmap."""
        m = math.ceil(-K * math.log(p) / (math.log(2) ** 2))
        n_bytes = math.ceil(m / 8)
        if len(data) != n_bytes:
            raise ValueError(f"Expected {n_bytes} bytes, got {len(data)}")
        return cls(K=K, p=p, _bits=bytearray(data))

    def size_bytes(self) -> int:
        return len(self._bits)

    def is_empty(self) -> bool:
        return not any(self._bits)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _positions(self, tag: bytes):
        """Yield k bit positions for tag using SHAKE256 with index suffix."""
        for i in range(self.k):
            h = hashlib.shake_256(tag + i.to_bytes(2, "big")).digest(4)
            pos = int.from_bytes(h, "big") % self.m
            yield pos
