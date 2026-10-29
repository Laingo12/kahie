"""Tests never call the network and never spend API credits, even when .env holds a real key.

- Before any test module imports the app: KAHIE_BACKEND=mock and a throw-away database.
- For every test: config.BACKEND is forced to "mock", and the OpenAI client and httpx.post raise
  if called. A test that needs a fake model or a fake HTTP answer patches them again itself
  (its monkeypatch runs after this one and wins).
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["KAHIE_BACKEND"] = "mock"
os.environ.setdefault("KAHIE_DB", str(Path(tempfile.mkdtemp()) / "test.db"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import config, models_client  # noqa: E402


def _no_network(*args, **kwargs):
    raise RuntimeError("network call in a test: patch models_client._client or httpx.post in the test")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(config, "BACKEND", "mock")
    monkeypatch.setattr(models_client, "_client", _no_network)
    monkeypatch.setattr(httpx, "post", _no_network)
    yield
