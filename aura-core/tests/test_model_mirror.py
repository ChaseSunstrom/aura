"""The server mirrors on-device model weights so the phone never leaves home.

The Android app can detect the wake word and transcribe locally. That needs
weights, and where they come from is a security decision rather than a
packaging one:

  * not in the APK — tens of megabytes for a feature most installs never turn
    on, and a new release every time a model changes;
  * **not from the internet either** — a phone fetching from GitHub tells a
    third party that this device is setting up a private voice assistant, down
    to which wake word, and it breaks the rule the app holds everywhere else:
    talk to the configured Jarvis and to nothing else, pinned host, bearer
    token. An exception for "just the download" is an exception in the code
    path that runs on a fresh, unconfigured install.

So jarvis-core is the mirror. It fetches once, on a machine that already
reaches the internet for `ollama pull`, checks the bytes against a digest
pinned in the repository, and serves them over the origin the phone already
trusts.

What these tests hold:
  1. the digests are real, and pinned — the only thing making a third-party
     download trustworthy;
  2. a name that is not in the catalogue cannot become a path;
  3. a mismatched download is refused rather than cached, because a truncated
     ONNX model fails as a wake word that never fires, which is
     indistinguishable from the feature being off;
  4. the routes are authenticated like everything else.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.api import models as model_store  # noqa: E402
from aura.core import Jarvis  # noqa: E402


def test_every_catalogue_entry_pins_a_real_digest():
    """A placeholder digest is worse than none: it fails closed, silently.

    Sixty-four hex characters, and not a repeated or obviously typed pattern —
    the failure this guards against is somebody adding an entry with a
    made-up hash, which turns the whole verification step into a permanent
    refusal that looks like a network problem.
    """
    assert model_store.CATALOGUE, "the catalogue is empty"
    seen: set[str] = set()
    for spec in model_store.CATALOGUE:
        assert re.fullmatch(r"[0-9a-f]{64}", spec.sha256), (
            f"{spec.name} has a digest that is not a sha256: {spec.sha256!r}"
        )
        assert spec.sha256 not in seen, f"{spec.name} reuses another entry's digest"
        seen.add(spec.sha256)
        assert spec.url.startswith(("https://", model_store.BUNDLED)), f"{spec.name} is fetched over cleartext"
        if spec.url.startswith(model_store.BUNDLED):
            # A bundled file is in the repository and is what its pin says.
            source = model_store.BUNDLED_DIR / spec.url[len(model_store.BUNDLED):]
            assert source.is_file(), f"{spec.name}: {source} is not in the repository"
            assert hashlib.sha256(source.read_bytes()).hexdigest() == spec.sha256, f"{spec.name} does not match its pin"
            assert source.stat().st_size == spec.bytes, f"{spec.name}'s size is not {spec.bytes}"
        assert spec.bytes > 0, f"{spec.name} claims to be zero bytes"
        assert spec.purpose, f"{spec.name} does not say what it is for"


def test_the_catalogue_is_the_path_validation(tmp_path):
    """Membership, not traversal-checking.

    Building a path from a request-supplied name and then arguing about whether
    it stayed inside a directory is the losing half of that argument. A fixed
    set of known names never enters it.
    """
    box = Jarvis(tmp_path)
    for hostile in (
        "../../etc/passwd",
        "/etc/passwd",
        "melspectrogram.onnx/../../../secrets.yaml",
        "..%2f..%2fetc%2fpasswd",
        "",
        "melspectrogram.ONNX",
    ):
        assert model_store.local_path(box, hostile) is None, hostile

    known = model_store.CATALOGUE[0].name
    resolved = model_store.local_path(box, known)
    assert resolved is not None
    assert resolved.parent == model_store.cache_dir(box)


async def test_an_unknown_model_is_refused_before_anything_is_fetched(tmp_path):
    box = Jarvis(tmp_path)
    with pytest.raises(model_store.ModelError) as err:
        await model_store.async_ensure(box, "definitely_not_a_model.onnx")
    assert "not a model" in str(err.value)
    assert not model_store.cache_dir(box).exists(), "a refused name still made a directory"


async def test_a_download_that_does_not_match_its_digest_is_not_cached(tmp_path, monkeypatch):
    """The whole point of the pin.

    A truncated or substituted ONNX model does not fail loudly on the phone. It
    fails as a wake word that never triggers — which the user cannot tell apart
    from the feature being switched off. So a mismatch must leave nothing
    behind to be served next time.
    """
    box = Jarvis(tmp_path)
    spec = model_store.CATALOGUE[0]

    def _write_something_else(_spec, target: Path) -> None:
        target.write_bytes(b"not the model you were looking for")

    monkeypatch.setattr(model_store, "_download", _write_something_else)

    with pytest.raises(model_store.ModelError) as err:
        await model_store.async_ensure(box, spec.name)
    assert "digest" in str(err.value)

    cached = model_store.cache_dir(box) / spec.name
    assert not cached.exists(), "a file that failed its digest was kept"
    assert not list(model_store.cache_dir(box).glob("*.part")), "a partial was left behind"
    assert model_store.is_cached(box, spec.name) is False


async def test_a_matching_download_is_cached_and_not_fetched_twice(tmp_path, monkeypatch):
    box = Jarvis(tmp_path)
    payload = b"pretend onnx"
    digest = hashlib.sha256(payload).hexdigest()
    spec = model_store.ModelSpec(
        name="melspectrogram.onnx",
        url="https://example.invalid/m.onnx",
        sha256=digest,
        purpose="test",
        bytes=len(payload),
    )
    monkeypatch.setitem(model_store.CATALOGUE_BY_NAME, spec.name, spec)

    calls = 0

    def _write(_spec, target: Path) -> None:
        nonlocal calls
        calls += 1
        target.write_bytes(payload)

    monkeypatch.setattr(model_store, "_download", _write)

    first = await model_store.async_ensure(box, spec.name)
    assert first.read_bytes() == payload
    assert model_store.is_cached(box, spec.name) is True

    second = await model_store.async_ensure(box, spec.name)
    assert second == first
    assert calls == 1, "a cached model was fetched again"


def test_the_catalogue_payload_says_what_is_here(tmp_path):
    box = Jarvis(tmp_path)
    rows = {row["name"]: row for row in model_store.catalogue_payload(box)}
    assert set(rows) == {spec.name for spec in model_store.CATALOGUE}
    for row in rows.values():
        assert row["cached"] is False
        assert row["bytes"] > 0
        # The digest is published so the phone can verify what it received
        # rather than trusting the transfer.
        assert re.fullmatch(r"[0-9a-f]{64}", str(row["sha256"]))


def test_the_model_routes_are_behind_the_same_token_as_everything_else():
    """Not a special case. An unauthenticated file server is a file server."""
    from aura.api import rest

    paths = {getattr(route, "path", "") for route in rest.api_router.routes}
    assert "/api/models/list" in paths
    assert "/api/models/{name}" in paths
    # The router itself carries the auth dependency; a route added outside it
    # would be reachable without a token.
    assert rest.api_router.dependencies, "the API router lost its auth dependency"


def test_nothing_reachable_from_a_request_can_extend_the_catalogue():
    """It is a list of URLs the server will fetch and write to disk.

    Which makes it exactly the kind of list that must not be writable from a
    request, or from the model.
    """
    import inspect

    source = inspect.getsource(model_store)
    assert "CATALOGUE: tuple[ModelSpec, ...]" in source, (
        "the catalogue is no longer a tuple, so it can be appended to"
    )
    assert isinstance(model_store.CATALOGUE, tuple)
    for name in ("async_ensure", "catalogue_payload", "local_path"):
        fn = getattr(model_store, name)
        body = inspect.getsource(fn)
        for mutation in ("CATALOGUE.append", "CATALOGUE_BY_NAME[", "CATALOGUE +="):
            assert mutation not in body, f"{name} mutates the catalogue"


async def test_a_bundled_model_is_copied_through_the_digest_check(tmp_path, monkeypatch):
    """The personas' wake words ship in the repository; serving one is a copy, verified."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = b"trained here"
    (bundle / "hey_test.onnx").write_bytes(payload)
    monkeypatch.setattr(model_store, "BUNDLED_DIR", bundle)
    spec = model_store.ModelSpec(name="hey_test.onnx", url="bundled:hey_test.onnx",
                                 sha256=hashlib.sha256(payload).hexdigest(), purpose="test", bytes=len(payload))
    monkeypatch.setitem(model_store.CATALOGUE_BY_NAME, spec.name, spec)
    box = Jarvis(tmp_path / "config")
    path = await model_store.async_ensure(box, spec.name)
    assert path.read_bytes() == payload
    wrong = model_store.ModelSpec(name="hey_wrong.onnx", url="bundled:../hey_test.onnx",
                                  sha256=spec.sha256, purpose="test", bytes=len(payload))
    monkeypatch.setitem(model_store.CATALOGUE_BY_NAME, wrong.name, wrong)
    with pytest.raises(model_store.ModelError):
        await model_store.async_ensure(box, wrong.name)


async def test_a_cached_copy_of_another_size_is_fetched_again(tmp_path, monkeypatch):
    """T-0893: the house's cache kept the 13.7 KB persona-head stubs after the bundle was
    fixed, and a non-empty cached file was served as it was, so every phone still got a
    head onnxruntime could not open. A copy that is not the catalogue's size is stale."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = b"retrained with its weights"
    (bundle / "hey_test.onnx").write_bytes(payload)
    monkeypatch.setattr(model_store, "BUNDLED_DIR", bundle)
    spec = model_store.ModelSpec(name="hey_test.onnx", url="bundled:hey_test.onnx",
                                 sha256=hashlib.sha256(payload).hexdigest(), purpose="test", bytes=len(payload))
    monkeypatch.setitem(model_store.CATALOGUE_BY_NAME, spec.name, spec)
    box = Jarvis(tmp_path / "config")
    stale = model_store.cache_dir(box) / spec.name
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stub")
    assert not model_store.is_cached(box, spec.name)
    path = await model_store.async_ensure(box, spec.name)
    assert path.read_bytes() == payload


def test_every_bundled_wake_word_is_in_the_tree_with_its_digest():
    """A bundled entry whose file is missing or retrained without its digest
    fails every phone's download, and only on the phone."""
    bundled = [s for s in model_store.CATALOGUE if s.url.startswith(model_store.BUNDLED)]
    assert {s.name for s in bundled} >= {"aura.onnx"}
    for spec in bundled:
        body = (model_store.BUNDLED_DIR / spec.url[len(model_store.BUNDLED):]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == spec.sha256, spec.name
        assert len(body) == spec.bytes, spec.name


def test_every_bundled_wake_word_carries_its_own_weights():
    """T-0874: the persona heads were exported with their weights in a `.onnx.data` beside
    them that never shipped, so onnxruntime refused each one ("cannot get file size") and no
    phone ever woke to "Hey Ada". An ONNX file pointing at external data names its file
    under the key "location"; a self-contained one never does."""
    for spec in (s for s in model_store.CATALOGUE if s.url.startswith(model_store.BUNDLED)):
        body = (model_store.BUNDLED_DIR / spec.url[len(model_store.BUNDLED):]).read_bytes()
        assert b"\x0a\x08location" not in body, f"{spec.name} keeps its weights in another file"


def test_the_phones_speech_model_is_mirrored_at_a_pinned_commit():
    """The phone's speech to text (2026-10-08) is three files that must move together:
    an encoder, a decoder and the tokenizer they were exported with. Each is fetched from
    one Hugging Face commit, not `main`, so an upstream re-export cannot pair a new
    decoder with an old tokenizer under pins that each still match."""
    names = ("moonshine-base-encoder.onnx", "moonshine-base-decoder.onnx", "moonshine-tokenizer.json")
    commits = set()
    for name in names:
        spec = model_store.CATALOGUE_BY_NAME[name]
        m = re.search(r"/resolve/([0-9a-f]{40})/", spec.url)
        assert m, f"{name} is not fetched at a commit: {spec.url}"
        commits.add(m.group(1))
    assert len(commits) == 1, f"the speech model's files come from different commits: {commits}"


def test_the_phones_offline_voices_are_mirrored_with_their_pins():
    """T-0618, T-0872: the voices are not in the APK or fetched by the build; the phone gets
    them from here, by the archive names BundledVoice.kt asks for."""
    kotlin = (Path(__file__).resolve().parents[2]
              / "android-app/app/src/main/kotlin/ai/jarvis/app/assist/BundledVoice.kt").read_text()
    asked = set(re.findall(r'"([\w.-]+\.tar\.bz2)"', kotlin))
    assert asked == {"vits-piper-en_GB-alan-medium.tar.bz2", "kokoro-int8-multi-lang-v1_0.tar.bz2"}
    for name in asked:
        spec = model_store.CATALOGUE_BY_NAME[name]
        assert spec.url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/")
        assert spec.bytes < model_store.MAX_BYTES


async def test_every_model_is_served_with_its_pinned_digest_header(tmp_path, monkeypatch):
    """The phone refuses a model that arrives without X-Jarvis-SHA256 (T-0618), so the
    route must send it for every catalogue entry, with the catalogue's own pin."""
    from aura.api import rest

    monkeypatch.setattr(rest, "get_jarvis", lambda request: Jarvis(tmp_path))
    for spec in model_store.CATALOGUE:
        path = tmp_path / spec.name
        path.write_bytes(b"x")

        async def ensure(jarvis, name, path=path):
            return path

        monkeypatch.setattr(model_store, "async_ensure", ensure)
        response = await rest.models_get(None, spec.name)
        assert response.headers["X-Jarvis-SHA256"] == spec.sha256, spec.name
