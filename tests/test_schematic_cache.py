"""Tests for spicebridge.schematic_cache."""

from spicebridge.schematic_cache import SchematicCache


class TestSchematicCache:
    def test_put_and_get_roundtrip(self):
        cache = SchematicCache()
        cache.put("abc", b"\x89PNG_data")
        assert cache.get("abc") == b"\x89PNG_data"

    def test_get_missing_returns_none(self):
        cache = SchematicCache()
        assert cache.get("nonexistent") is None

    def test_fifo_eviction(self):
        cache = SchematicCache(max_size=2)
        cache.put("a", b"A")
        cache.put("b", b"B")
        cache.put("c", b"C")  # evicts "a"
        assert cache.get("a") is None
        assert cache.get("b") == b"B"
        assert cache.get("c") == b"C"

    def test_put_existing_key_refreshes_order(self):
        cache = SchematicCache(max_size=2)
        cache.put("a", b"A")
        cache.put("b", b"B")
        cache.put("a", b"A2")  # refresh "a", now "b" is oldest
        cache.put("c", b"C")  # evicts "b"
        assert cache.get("a") == b"A2"
        assert cache.get("b") is None
        assert cache.get("c") == b"C"

    def test_len_tracking(self):
        cache = SchematicCache(max_size=3)
        assert len(cache) == 0
        cache.put("a", b"A")
        assert len(cache) == 1
        cache.put("b", b"B")
        assert len(cache) == 2
        cache.put("c", b"C")
        assert len(cache) == 3
        cache.put("d", b"D")  # evicts "a"
        assert len(cache) == 3

    def test_delete_existing(self):
        cache = SchematicCache()
        cache.put("x", b"X")
        cache.delete("x")
        assert cache.get("x") is None
        assert len(cache) == 0

    def test_delete_missing_is_noop(self):
        cache = SchematicCache()
        cache.delete("nonexistent")  # should not raise
