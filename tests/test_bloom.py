"""Tests for BloomFilter."""
import pytest
from attest.bloom_filter import BloomFilter


def _tag(s: str) -> bytes:
    return s.encode()


def test_insert_query():
    bf = BloomFilter(K=100, p=0.01)
    bf.insert(_tag("alice"))
    assert bf.query(_tag("alice")) is True


def test_absent():
    bf = BloomFilter(K=100, p=0.01)
    bf.insert(_tag("alice"))
    # "bob" almost certainly absent (p=1% FP rate, single element)
    assert bf.query(_tag("bob")) is False


def test_merge():
    bf1 = BloomFilter(K=100, p=0.01)
    bf2 = BloomFilter(K=100, p=0.01)
    bf1.insert(_tag("alice"))
    bf2.insert(_tag("bob"))
    bf1.merge(bf2)
    assert bf1.query(_tag("alice")) is True
    assert bf1.query(_tag("bob"))   is True


def test_serialize_roundtrip():
    bf = BloomFilter(K=1000, p=0.01)
    for i in range(10):
        bf.insert(f"tag_{i}".encode())
    raw = bf.serialize()
    bf2 = BloomFilter.deserialize(raw, K=1000, p=0.01)
    for i in range(10):
        assert bf2.query(f"tag_{i}".encode()) is True


def test_size_bytes_k10000():
    bf = BloomFilter(K=10_000, p=0.01)
    # Expected: ceil(95851/8) = 11982 bytes ≈ 12 KB
    assert 11_900 <= bf.size_bytes() <= 12_100, f"Unexpected size: {bf.size_bytes()}"


def test_empty_flag():
    bf = BloomFilter(K=100, p=0.01)
    assert bf.is_empty()
    bf.insert(b"x")
    assert not bf.is_empty()


def test_no_false_negatives():
    """Once inserted, a tag must always be found."""
    bf = BloomFilter(K=500, p=0.01)
    tags = [f"cred_{i}".encode() for i in range(200)]
    for t in tags:
        bf.insert(t)
    for t in tags:
        assert bf.query(t) is True, f"False negative for {t}"
