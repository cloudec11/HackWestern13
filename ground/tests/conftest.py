"""Shared test helpers: an in-process client for the stdlib WSGI app.

Mirrors the small part of the FastAPI/requests TestClient API the tests use
(get, post, .status_code, .json(), .text), so no third-party packages are needed.
"""
import io
import json as _json
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults

import pytest

from ground.api import create_app


class Response:
    def __init__(self, status: str, headers: list, body: bytes):
        self.status_code = int(status.split()[0])
        self.headers = dict(headers)
        self.content = body
        self.text = body.decode("utf-8", errors="replace")

    def json(self):
        return _json.loads(self.text)


class Client:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, params=None, json=None, content=None, headers=None):
        body = _json.dumps(json).encode() if json is not None else (content or b"")
        environ = {}
        setup_testing_defaults(environ)
        environ.update({
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": urlencode(params or {}),
            "CONTENT_TYPE": (headers or {}).get("content-type", "application/json"),
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
        })
        captured = {}

        def start_response(status, hdrs, exc_info=None):
            captured["status"], captured["headers"] = status, hdrs

        out = b"".join(self.app(environ, start_response))
        return Response(captured["status"], captured["headers"], out)

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def post(self, path, json=None, content=None, headers=None):
        return self.request("POST", path, json=json, content=content, headers=headers)


@pytest.fixture
def client(tmp_path):
    return Client(create_app(tmp_path / "test.sqlite3"))
