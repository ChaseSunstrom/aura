"""The refused-utterance store does what its docstring says: memory, two hours, a cap, close voices."""
from __future__ import annotations

from aura_adapter import speaker as sp


def _verdict(similarity: float, nearest: str = "Ted") -> dict:
    return {"dropped": True, "nearest": nearest, "similarity": similarity}


def test_a_close_refusal_is_held_and_a_far_one_is_not() -> None:
    held = sp.Unrecognised()
    assert held.hold(b"clip", "audio/wav", _verdict(0.5), "phone", now=1000.0)
    far = _verdict(sp.Unrecognised.FLOOR - 0.01)
    assert held.hold(b"clip", "audio/wav", far, "phone", now=1000.0) is None
    assert [c["nearest"] for c in held.listing(now=1000.0)] == ["Ted"]


def test_a_held_clip_is_gone_after_two_hours_and_the_window_is_two_hours() -> None:
    assert sp.Unrecognised.TTL_S == 2 * 60 * 60
    store = sp.Unrecognised()
    store.hold(b"clip", "audio/wav", _verdict(0.5), "car", now=1000.0)
    assert len(store.listing(now=1000.0 + sp.Unrecognised.TTL_S)) == 1
    assert store.listing(now=1000.0 + sp.Unrecognised.TTL_S + 1) == []


def test_take_removes_the_clip_and_returns_its_audio() -> None:
    store = sp.Unrecognised()
    claim = store.hold(b"clip", "audio/wav", _verdict(0.5), "car", now=1000.0)
    taken = store.take(claim, now=1001.0)
    assert taken is not None and taken["audio"] == b"clip"
    assert store.take(claim, now=1001.0) is None


def test_at_most_keep_clips_are_held_and_the_oldest_go_first() -> None:
    store = sp.Unrecognised()
    ids = [store.hold(b"c%d" % i, "audio/wav", _verdict(0.5), "car", now=1000.0 + i)
           for i in range(sp.Unrecognised.KEEP + 1)]
    listed = store.listing(now=1000.0 + sp.Unrecognised.KEEP)
    assert len(listed) == sp.Unrecognised.KEEP
    assert ids[0] not in {c["id"] for c in listed}


def test_a_new_store_starts_empty_because_nothing_is_persisted() -> None:
    first = sp.Unrecognised()
    first.hold(b"clip", "audio/wav", _verdict(0.5), "car", now=1000.0)
    assert sp.Unrecognised().listing(now=1000.0) == []
