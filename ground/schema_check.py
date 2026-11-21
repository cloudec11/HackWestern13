"""Minimal JSON Schema validator, standard library only (agent-3).

Validates against shared/event_schema.json without the `jsonschema` package,
which isn't available on the QNX Pi. It covers only the keywords that schema
uses. Loading a schema with any other validation keyword is an error, so a
later contract change can't be silently ignored.

    from ground.schema_check import load_validators
    v = load_validators()             # {"event_batch": Validator, "heartbeat": Validator, ...}
    errors = v["event_batch"].errors(body)   # [] if valid, else [(path, message), ...]

Semantics follow JSON Schema draft 2020-12 for the supported keywords.
ground/tests/test_schema_check.py cross-checks it against the real `jsonschema`
library on the laptop.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "shared" / "event_schema.json"

VALIDATION_KEYWORDS = {
    "$ref", "type", "properties", "required", "additionalProperties", "dependentRequired",
    "items", "minItems", "maxItems", "minimum", "maximum", "minLength", "maxLength",
    "pattern", "enum", "const", "not",
}
ANNOTATION_KEYWORDS = {"$schema", "$id", "$defs", "title", "description", "examples", "x-version"}

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    # 2020-12: an integer is any number with no fractional part (so 5.0 counts)
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool))
                         or (isinstance(v, float) and v.is_integer()),
}


class SchemaError(ValueError):
    pass


class Validator:
    def __init__(self, root: dict, schema: dict):
        self.root = root
        self.schema = schema

    def errors(self, value: Any) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self._check(self.schema, value, "", out)
        return out

    def is_valid(self, value: Any) -> bool:
        return not self.errors(value)

    # ---- internals ----

    def _resolve(self, ref: str) -> dict:
        if not ref.startswith("#/"):
            raise SchemaError(f"only local $ref supported, got {ref!r}")
        node: Any = self.root
        for part in ref[2:].split("/"):
            node = node[part]
        return node

    def _check(self, s: dict, v: Any, path: str, out: list) -> None:
        def err(msg: str, p: str = path) -> None:
            out.append((p or "(root)", msg))

        if "$ref" in s:
            self._check(self._resolve(s["$ref"]), v, path, out)

        if "type" in s:
            types = s["type"] if isinstance(s["type"], list) else [s["type"]]
            if not any(_TYPES[t](v) for t in types):
                err(f"expected {' or '.join(types)}, got {_json_type(v)}")
                return  # further checks on a wrong type only add noise

        if "enum" in s and not any(_equal(v, e) for e in s["enum"]):
            err(f"{v!r} is not one of {s['enum']}")
        if "const" in s and not _equal(v, s["const"]):
            err(f"must be {s['const']!r}")
        if "not" in s:
            sub: list = []
            self._check(s["not"], v, path, sub)
            if not sub:
                err(f"{v!r} is not allowed")

        if _TYPES["number"](v):
            if "minimum" in s and v < s["minimum"]:
                err(f"{v} is less than the minimum {s['minimum']}")
            if "maximum" in s and v > s["maximum"]:
                err(f"{v} is more than the maximum {s['maximum']}")

        if isinstance(v, str):
            n = len(v)
            if "minLength" in s and n < s["minLength"]:
                err(f"shorter than {s['minLength']} characters")
            if "maxLength" in s and n > s["maxLength"]:
                err(f"longer than {s['maxLength']} characters")
            if "pattern" in s and not re.search(s["pattern"], v):
                err(f"{v!r} does not match pattern {s['pattern']}")

        if isinstance(v, list):
            if "minItems" in s and len(v) < s["minItems"]:
                err(f"needs at least {s['minItems']} items, got {len(v)}")
            if "maxItems" in s and len(v) > s["maxItems"]:
                err(f"allows at most {s['maxItems']} items, got {len(v)}")
            if "items" in s:
                for i, item in enumerate(v):
                    self._check(s["items"], item, f"{path}/{i}" if path else str(i), out)

        if isinstance(v, dict):
            props = s.get("properties", {})
            for k in s.get("required", []):
                if k not in v:
                    err(f"missing required field {k!r}")
            for k, deps in s.get("dependentRequired", {}).items():
                if k in v:
                    for d in deps:
                        if d not in v:
                            err(f"{d!r} is required when {k!r} is present")
            if s.get("additionalProperties") is False:
                for k in v:
                    if k not in props:
                        err(f"unknown field {k!r}")
            for k, sub_schema in props.items():
                if k in v:
                    self._check(sub_schema, v[k], f"{path}/{k}" if path else k, out)


def _json_type(v: Any) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _TYPES[name](v):
            return name
    return type(v).__name__


def _equal(a: Any, b: Any) -> bool:
    # JSON equality: True != 1, but 1 == 1.0
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def _audit(node: Any, where: str) -> None:
    """Refuse keywords this validator doesn't implement."""
    if isinstance(node, dict):
        for k, val in node.items():
            if k in ("properties", "$defs", "dependentRequired"):
                if k != "dependentRequired":
                    for name, sub in val.items():
                        _audit(sub, f"{where}/{k}/{name}")
                continue
            if k in ("examples",):
                continue
            if k not in VALIDATION_KEYWORDS | ANNOTATION_KEYWORDS:
                raise SchemaError(f"unsupported schema keyword {k!r} at {where or '/'}")
            if k in ("items", "not"):
                _audit(val, f"{where}/{k}")
            if k == "additionalProperties" and val is not False:
                raise SchemaError(f"only additionalProperties: false is supported (at {where})")


def load_validators(path: Path = SCHEMA_PATH) -> dict[str, Validator]:
    root = json.loads(Path(path).read_text(encoding="utf-8"))
    _audit(root, "")
    return {name: Validator(root, {"$ref": f"#/$defs/{name}"}) for name in root.get("$defs", {})}
