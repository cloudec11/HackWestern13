"""Cross-check ground/schema_check.py against the real jsonschema library.

The stdlib validator must accept and reject exactly what jsonschema does for
shared/event_schema.json. Skipped where jsonschema isn't installed (e.g. the Pi).
"""
import copy
import json
import random

import pytest

from ground.fake_pod import DEFAULT_RATES, FakePod
from ground.schema_check import SCHEMA_PATH, SchemaError, Validator, _audit, load_validators

jsonschema = pytest.importorskip("jsonschema")
from referencing import Registry, Resource  # noqa: E402  (ships with jsonschema)

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
OURS = load_validators()
REG = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))


def ref(name):
    return jsonschema.Draft202012Validator({"$ref": f"{SCHEMA['$id']}#/$defs/{name}"}, registry=REG)


THEIRS = {name: ref(name) for name in SCHEMA["$defs"]}


def agree(name, value):
    ours, theirs = OURS[name].is_valid(value), THEIRS[name].is_valid(value)
    assert ours == theirs, f"{name}: ours={ours} jsonschema={theirs} for {value!r}\n{OURS[name].errors(value)}"
    return ours


def good_samples():
    p = FakePod("pod-01", dict(DEFAULT_RATES), {}, seed=3, mobile=True)
    evs = [p.make_visit(p.pick_species(), 1795290000000 + i * 5000) for i in range(60)]
    hb = []
    p.heartbeat(lambda path, body: hb.append(body) or {"pod_id": "pod-01", "acked_seq": 0}, 1795290000000)
    ex = SCHEMA["examples"][0]
    return {
        "visit_event": evs + [{k: v for k, v in e.items() if k not in ("lat", "lon", "loc_source")} for e in evs[:5]],
        "event_batch": [{"pod_id": "pod-01", "events": evs[:50]}, ex["event_batch"]],
        "heartbeat": hb + [ex["heartbeat"]],
        "event_ack": [ex["event_ack"]],
        "heartbeat_ack": [ex["heartbeat_ack"]],
    }


JUNK = [None, True, False, 0, -1, 1.5, 5.0, 1e30, "", "x", "Person", "person", "a" * 70, [], {}, [1], {"a": 1}]


def mutations(obj, rng):
    """Yield variants of obj: each field removed, each field replaced by junk, extra fields."""
    for k in list(obj):
        m = copy.deepcopy(obj); del m[k]; yield m
        for j in JUNK:
            m = copy.deepcopy(obj); m[k] = j; yield m
    m = copy.deepcopy(obj); m["extra_field"] = 1; yield m
    for _ in range(20):                      # random numeric nudges around the limits
        m = copy.deepcopy(obj)
        k = rng.choice(list(m))
        if isinstance(m[k], (int, float)) and not isinstance(m[k], bool):
            m[k] = rng.choice([-1, 0, 1, 2**32, 2**32 - 1, 65535, 65536, 0.5, 1.0000001, 125, 126, -40, -41,
                               90, 90.5, -180, 181, 1600000000000, 1599999999999])
            yield m


def test_valid_samples_agree():
    for name, samples in good_samples().items():
        for s in samples:
            assert agree(name, s), f"{name} sample rejected: {OURS[name].errors(s)}"


@pytest.mark.parametrize("name", ["visit_event", "heartbeat", "event_ack", "heartbeat_ack"])
def test_mutations_agree(name):
    rng = random.Random(name)
    n = 0
    for base in good_samples()[name]:
        for m in mutations(base, rng):
            agree(name, m); n += 1
    assert n > 30   # sanity: the mutation generator really ran


def test_batch_mutations_agree():
    base = good_samples()["event_batch"][0]
    agree("event_batch", {"pod_id": "pod-01", "events": []})
    agree("event_batch", {"pod_id": "pod-01", "events": base["events"] + base["events"][:1]})  # 51
    agree("event_batch", {"pod_id": "pod-01", "events": "nope"})
    agree("event_batch", [base])
    for i in range(10):
        m = copy.deepcopy(base); m["events"][i]["species"] = "person"; agree("event_batch", m)
        m = copy.deepcopy(base); del m["events"][i]["seq"]; agree("event_batch", m)
        m = copy.deepcopy(base); m["events"][i]["loc_source"] = "gps"; m["events"][i].pop("lat"); agree("event_batch", m)


def test_error_paths_are_readable():
    base = good_samples()["event_batch"][0]
    base["events"][3]["species"] = "person"
    paths = [p for p, _ in OURS["event_batch"].errors(base)]
    assert paths == ["events/3/species"]


def test_unsupported_keyword_is_refused():
    with pytest.raises(SchemaError):
        _audit({"$defs": {"x": {"type": "object", "oneOf": []}}}, "")
    with pytest.raises(SchemaError):
        _audit({"$defs": {"x": {"type": "object", "additionalProperties": {"type": "string"}}}}, "")
