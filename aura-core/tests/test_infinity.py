"""A number that arrives as Infinity (or NaN, or 1e999) in someone else's JSON must not raise (T-1345).

Python's `json` reads the literal `Infinity` and `1e999` as inf, and `int(inf)`
raises OverflowError, which `except (TypeError, ValueError)` lets through. Each
test feeds the reader a remote answer carrying one and asserts the decision: a
refusal at the door, or the field dropped, never an unhandled exception.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from aura import calls, messages
from aura.helpers.finite_json import loads_finite
from aura.integrations.channels.adapters import TelegramChannel
from aura.integrations.mcp.catalog import server_from_dict
from aura.integrations.mqtt.entity import _as_float, _as_int
from aura.llm import catalogue
from aura.llm.openai_compat import OllamaError
from aura.llm.rerank import Reranker
from aura.voice.wyoming import WyomingError, async_read_event, decode_header

BAD_NUMBERS = ["Infinity", "-Infinity", "NaN", "1e999", "-1e999"]


@pytest.mark.parametrize("literal", BAD_NUMBERS)
def test_loads_finite_refuses_a_non_finite_number(literal):
    with pytest.raises(ValueError):
        loads_finite(f'{{"limit": {literal}}}')


def test_loads_finite_keeps_ordinary_numbers():
    assert loads_finite('{"a": 1, "b": 2.5, "c": -3e2}') == {"a": 1, "b": 2.5, "c": -300.0}


class _Request:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    async def body(self) -> bytes:
        return self._raw


@pytest.mark.asyncio
@pytest.mark.parametrize("literal", BAD_NUMBERS)
async def test_a_rest_body_carrying_a_non_finite_number_is_a_400(literal):
    from aura.api.rest import json_body

    with pytest.raises(HTTPException) as caught:
        await json_body(_Request(f'{{"brightness": {literal}}}'.encode()))
    assert caught.value.status_code == 400


@pytest.mark.parametrize("literal", BAD_NUMBERS)
def test_a_wyoming_header_with_a_non_finite_length_is_a_protocol_error(literal):
    with pytest.raises(WyomingError):
        decode_header(f'{{"type": "audio-chunk", "payload_length": {literal}}}'.encode())


@pytest.mark.asyncio
async def test_a_wyoming_data_block_with_a_non_finite_number_is_a_protocol_error():
    block = b'{"rate": Infinity}'
    header = json.dumps({"type": "audio-start", "data_length": len(block)}).encode() + b"\n"
    reader = asyncio.StreamReader()
    reader.feed_data(header + block)
    reader.feed_eof()
    with pytest.raises(WyomingError):
        await async_read_event(reader)


@pytest.mark.asyncio
async def test_a_wyoming_event_with_an_infinite_payload_length_is_a_protocol_error():
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"type": "audio-chunk", "payload_length": 1e999}\n')
    reader.feed_eof()
    with pytest.raises(WyomingError):
        await async_read_event(reader)


@pytest.mark.parametrize("field", ["size", "n_ctx_train"])
def test_a_llama_cpp_model_with_an_infinite_size_keeps_the_rest(field):
    info = catalogue._openai_entry({"id": "m", "meta": {field: float("inf"), "n_params": 7e9}})
    assert info is not None and info.id == "m"
    assert info.size_bytes is None and info.context is None


def test_a_vllm_model_with_an_infinite_context_keeps_the_rest():
    info = catalogue._openai_entry({"id": "m", "root": "w", "max_model_len": float("inf")})
    assert info is not None and info.context is None


class _Probe:
    def __init__(self, answers):
        self.answers = answers

    async def get(self, url, headers=None):
        for suffix, answer in self.answers.items():
            if url.endswith(suffix):
                return answer
        return RuntimeError("no route")


@pytest.mark.asyncio
async def test_an_ollama_model_with_an_infinite_size_is_still_listed():
    probe = _Probe({"/api/tags": {"models": [{"name": "m:1", "size": float("inf")}]}})
    report = catalogue.ServerReport(url="http://10.0.0.9:11434", kind="ollama")
    models = await catalogue._read_ollama(probe, "http://10.0.0.9:11434", report)
    assert models["m:1"].size_bytes is None and report.ok


@pytest.mark.asyncio
async def test_a_tei_server_with_an_infinite_input_length_is_still_read():
    probe = _Probe({"/info": {"model_id": "e", "max_input_length": float("inf")}})
    report = catalogue.ServerReport(url="http://10.0.0.9:8080", kind="tei")
    info = await catalogue._read_tei(probe, "http://10.0.0.9:8080", "e", "embeddings", report)
    assert info.id == "e" and info.context is None and report.ok


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _Http:
    def __init__(self, payload):
        self._payload = payload

    async def post(self, url, json=None, timeout=None):
        return _Response(self._payload)


@pytest.mark.asyncio
async def test_a_rerank_row_with_a_non_finite_number_is_skipped_not_fatal():
    rows = [
        {"index": float("inf"), "score": 1.0},
        {"index": 1, "score": 9.0},
        {"index": 0, "score": float("nan")},
    ]
    reranker = Reranker(url="http://rerank", client=_Http(rows))
    # The one good row leads: an answer of [0, 1, 2] would also come from skipping every row.
    assert await reranker.order("q", ["a", "b", "c"]) == [1, 0, 2]


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), "inf", "nan"])
def test_a_device_number_that_is_not_finite_reads_as_absent(value):
    assert _as_float(value) is None
    assert _as_float(value, 4.0) == 4.0
    assert _as_int(value) is None
    assert _as_int(value, 7) == 7


@pytest.mark.asyncio
async def test_a_telegram_update_with_an_infinite_id_is_skipped_not_fatal():
    class _Answer:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": [
                {"update_id": float("inf"), "message": {"text": "x", "from": {"id": 1}}},
                {"update_id": 5, "message": {"text": "hi", "from": {"id": 2}}},
            ]}

    class _Client:
        async def post(self, url, json=None, timeout=None):
            return _Answer()

    channel = TelegramChannel(token="t", client=_Client())
    got = await channel.poll()
    assert got == [{"sender": "2", "text": "hi"}]
    assert channel._offset == 6


def test_a_message_with_an_infinite_time_is_kept_with_time_zero():
    kept = messages.clean({"id": "1", "text": "hi", "conversation": "c", "source": "sms",
                           "at": float("inf")})
    assert kept is not None and kept["at"] == 0


def test_a_call_with_an_infinite_time_is_kept_with_time_zero():
    kept = calls.clean({"id": "1", "state": "ringing", "from": "Ann", "at": float("inf")})
    assert kept is not None and kept["at"] == 0


def test_an_mcp_server_with_an_infinite_tier_gets_the_default_tier():
    spec = server_from_dict({"name": "svc", "url": "http://10.0.0.9:9000/mcp",
                             "tier": float("inf")})
    assert spec is not None and spec.tier == 2


# --- T-1352: the readers T-1345 left open -----------------------------------
def test_an_mqtt_payload_carrying_nan_keeps_its_other_readings_t1364():
    # T-1364 replaces T-1352's "reads as not JSON": the one bad reading is missing, not the lot.
    from aura.integrations.mqtt.client import MqttMessage
    from aura.integrations.mqtt.translators import _json

    assert MqttMessage("t", '{"battery": NaN, "h": 40}').json("kept") == {"battery": None, "h": 40}
    assert MqttMessage("t", '{"battery": 1e999}').json("kept") == {"battery": None}
    assert MqttMessage("t", '[-Infinity, 2.5]').json("kept") == [None, 2.5]
    assert _json('{"battery": Infinity}') == {"battery": None}
    assert MqttMessage("t", '{"battery": 88}').json() == {"battery": 88}
    assert MqttMessage("t", "{broken").json("kept") == "kept"


def test_loads_device_maps_non_finite_to_none_and_keeps_finite_numbers():
    from aura.helpers.finite_json import loads_device

    assert loads_device('{"a": NaN, "b": [Infinity, -Infinity, -1e999], "c": 1.5, "d": 2}') == {
        "a": None, "b": [None, None, None], "c": 1.5, "d": 2}
    with pytest.raises(ValueError):
        loads_device("{broken")


def test_an_mqtt_template_reads_a_nan_reading_as_missing_and_the_finite_one_as_itself():
    from aura.integrations.mqtt.entity import render_value_template

    payload = '{"temperature": NaN, "humidity": 40}'
    assert render_value_template("{{ value_json.temperature }}", payload, default="d") == "d"
    assert render_value_template("{{ value_json.humidity }}", payload, default="d") == "40"


@pytest.mark.asyncio
async def test_an_mqtt_event_given_a_nan_payload_is_not_named_after_the_raw_text(tmp_path):
    from aura.core import Jarvis
    from aura.integrations import mqtt as mqtt_integration
    from aura.integrations.mqtt.client import FakeMqttClient

    jarvis = Jarvis(tmp_path)
    client = FakeMqttClient()
    jarvis.data["mqtt"] = client
    await mqtt_integration.async_setup(jarvis, {"discovery": False, "event": [{
        "name": "Dial", "state_topic": "d/state"}]})
    # Parses now, and names no event: nothing fires, and the text is not the type.
    await client.feed("d/state", '{"battery": NaN}')
    # Truncated JSON never parses; it must not become the event type either.
    await client.feed("d/state", '{"event_type": "pre')
    assert jarvis.states.get("event.dial").state != '{"battery": NaN}'
    assert "event_type" not in jarvis.states.get("event.dial").attributes
    # A NaN reading beside a real event keeps the event and drops the reading to missing.
    await client.feed("d/state", '{"event_type": "press", "battery": NaN}')
    state = jarvis.states.get("event.dial")
    assert state.state == "press" and "battery" not in state.attributes
    # A plain-text event type still works.
    await client.feed("d/state", "double")
    assert jarvis.states.get("event.dial").state == "double"


def test_an_mqtt_payload_with_a_nan_reading_still_refused_at_the_other_doors_t1364():
    with pytest.raises(ValueError):
        loads_finite('{"battery": NaN}')


@pytest.mark.asyncio
async def test_a_discovery_payload_carrying_infinity_registers_nothing(tmp_path):
    from aura.core import Jarvis
    from aura.integrations import mqtt as mqtt_integration
    from aura.integrations.mqtt.client import FakeMqttClient

    jarvis = Jarvis(tmp_path)
    client = FakeMqttClient()
    jarvis.data["mqtt"] = client
    assert await mqtt_integration.async_setup(jarvis, {"discovery": True})
    topic = "homeassistant/sensor/x/config"
    await client.feed(topic, '{"name": "X", "state_topic": "x/s", "expire_after": Infinity}')
    assert jarvis.states.get("sensor.x") is None
    # Control: the same payload with a finite number does register.
    await client.feed(topic, '{"name": "X", "state_topic": "x/s", "expire_after": 60}')
    assert jarvis.states.get("sensor.x") is not None


@pytest.mark.asyncio
async def test_an_mqtt_attribute_payload_carrying_nan_merges_the_rest_as_missing(tmp_path):
    from aura.core import Jarvis
    from aura.integrations import mqtt as mqtt_integration
    from aura.integrations.mqtt.client import FakeMqttClient

    jarvis = Jarvis(tmp_path)
    client = FakeMqttClient()
    jarvis.data["mqtt"] = client
    await mqtt_integration.async_setup(jarvis, {"discovery": False, "sensor": [{
        "name": "Garden", "state_topic": "g/state", "json_attributes_topic": "g/attrs"}]})
    await client.feed("g/attrs", '{"battery": NaN, "humidity": 61}')
    attributes = jarvis.states.get("sensor.garden").attributes
    # A None attribute is not published (Entity drops it), so the NaN reading is absent.
    assert "battery" not in attributes and attributes["humidity"] == 61
    await client.feed("g/attrs", '{"battery": 88}')
    assert jarvis.states.get("sensor.garden").attributes["battery"] == 88


@pytest.mark.asyncio
async def test_an_mqtt_tracker_location_carrying_nan_keeps_the_finite_coordinate(tmp_path):
    from aura.core import Jarvis
    from aura.integrations import mqtt as mqtt_integration
    from aura.integrations.mqtt.client import FakeMqttClient

    jarvis = Jarvis(tmp_path)
    client = FakeMqttClient()
    jarvis.data["mqtt"] = client
    await mqtt_integration.async_setup(jarvis, {"discovery": False, "device_tracker": [{
        "name": "Car", "state_topic": "c/state", "json_attributes_topic": "c/loc"}]})
    await client.feed("c/loc", '{"latitude": NaN, "longitude": 2.5}')
    attributes = jarvis.states.get("device_tracker.car").attributes
    assert "latitude" not in attributes and attributes["longitude"] == 2.5


@pytest.mark.asyncio
@pytest.mark.parametrize("update_id", [1e300, 10**30, -1])
async def test_a_telegram_update_id_out_of_bound_is_skipped_and_later_updates_land(update_id):
    class _Answer:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": [
                {"update_id": update_id, "message": {"text": "x", "from": {"id": 1}}},
                {"update_id": 5, "message": {"text": "hi", "from": {"id": 2}}},
            ]}

    class _Client:
        async def post(self, url, json=None, timeout=None):
            return _Answer()

    channel = TelegramChannel(token="t", client=_Client())
    assert await channel.poll() == [{"sender": "2", "text": "hi"}]
    # A 1e300 offset would make Telegram drop every later update for good.
    assert channel._offset == 6


async def _read_wyoming(header: bytes, body: bytes = b""):
    reader = asyncio.StreamReader()
    reader.feed_data(header + b"\n" + body)
    reader.feed_eof()
    return await async_read_event(reader)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["data_length", "payload_length"])
@pytest.mark.parametrize("length", ["1e12", "[3]", '"7"', "true", "-5", "{}"])
async def test_a_wyoming_length_out_of_bound_or_not_a_number_is_a_protocol_error(field, length):
    with pytest.raises(WyomingError):
        await _read_wyoming(f'{{"type": "audio-chunk", "{field}": {length}}}'.encode())


@pytest.mark.asyncio
async def test_a_wyoming_length_within_bound_still_reads():
    event = await _read_wyoming(b'{"type": "audio-chunk", "payload_length": 3}', b"abc")
    assert event is not None and event.payload == b"abc"


@pytest.mark.parametrize("digits", ["²", "٣", "²⁰⁰", pytest.param("9" * 5000, id="long")])
def test_a_context_flag_that_is_not_plain_digits_is_ignored(digits):
    info = catalogue.ModelInfo(id="m")
    catalogue._from_command(f"vllm serve org/m --max-model-len {digits}", info)
    assert info.context is None


def test_a_context_flag_of_plain_digits_is_read():
    info = catalogue.ModelInfo(id="m")
    catalogue._from_command("vllm serve org/m --max-model-len 32768", info)
    assert info.context == 32768


@pytest.mark.parametrize("index", [float("inf"), float("nan"), 1e300, -1, "x", [1]])
def test_a_tool_call_fragment_with_an_unusable_index_is_dropped_not_filed_by_position(index):
    from aura.llm.openai_compat import _ToolCallBuffer

    buffer = _ToolCallBuffer()
    buffer.absorb([
        {"index": 0, "id": "a", "function": {"name": "good", "arguments": "{}"}},
        {"index": index, "id": "b", "function": {"name": "stray", "arguments": "{}"}},
    ])
    assert [call.name for call in buffer.finish()] == ["good"]


def test_a_tool_call_fragment_with_no_index_still_falls_back_to_position():
    from aura.llm.openai_compat import _ToolCallBuffer

    buffer = _ToolCallBuffer()
    buffer.absorb([{"id": "a", "function": {"name": "solo", "arguments": "{}"}}])
    assert [call.name for call in buffer.finish()] == ["solo"]


@pytest.mark.asyncio
async def test_an_embedding_with_an_infinite_index_is_a_clean_error():
    import httpx

    from aura.llm.openai_compat import OpenAICompatClient

    body = '{"data": [{"index": Infinity, "embedding": [1.0]}, {"index": 0, "embedding": [2.0]}]}'
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=body.encode(), headers={"content-type": "application/json"}))
    client = OpenAICompatClient(url="http://10.0.0.9:8000", transport=transport)
    with pytest.raises(OllamaError):
        await client.embed(["a", "b"])
