"""Planner against the real API shape, with a fake client (no network).
The Meta Model API only accepts tool_choice="auto": the model may reply in text instead of calling a tool."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("KAHIE_DB", str(Path(tempfile.mkdtemp()) / "test.db"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import config, models_client  # noqa: E402

CTX = {"lexicon": "", "products": "vary", "customers": "Rabe", "product_list": ["vary"], "customer_list": ["Rabe"]}


def _resp(tool_calls=None, content=None):
    return NS(choices=[NS(message=NS(tool_calls=tool_calls, content=content))])


def _call(name, args):
    return NS(function=NS(name=name, arguments=args))


class FakeClient:
    def __init__(self, resp=None, error=None):
        self.kwargs, self.resp, self.error = None, resp, error
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.resp


def _use(monkeypatch, fake):
    monkeypatch.setattr(config, "BACKEND", "api")
    monkeypatch.setattr(models_client, "_client", lambda open_weights=False: fake)


def test_uses_tool_choice_auto_and_a_large_token_budget(monkeypatch):
    fake = FakeClient(_resp([_call("record_sale", '{"customer": "Rabe", "items": [{"product": "vary", "qty": 3}], "paid": 2000}')]))
    _use(monkeypatch, fake)
    calls = models_client.plan("Rabe naka 3 kapoaka vary, nandoa 2000 Ar", CTX)
    assert calls == [{"tool": "record_sale", "args": {"customer": "Rabe", "items": [{"product": "vary", "qty": 3}], "paid": 2000}}]
    assert fake.kwargs["tool_choice"] == "auto" and fake.kwargs["max_tokens"] >= 4000


def test_text_reply_becomes_a_question_not_a_write(monkeypatch):
    _use(monkeypatch, FakeClient(_resp(None, "Firy kapoaka ?")))
    assert models_client.plan("Rabe naka vary", CTX) == [{"tool": "ask_clarification", "args": {"question": "Firy kapoaka ?"}}]


def test_empty_reply_and_bad_json_are_safe(monkeypatch):
    _use(monkeypatch, FakeClient(_resp([_call("record_sale", "{not json")], "")))
    calls = models_client.plan("…", CTX)
    assert calls[0]["tool"] == "ask_clarification" and calls[0]["args"]["question"]


def test_api_error_asks_to_retry(monkeypatch):
    _use(monkeypatch, FakeClient(error=TimeoutError("slow")))
    calls = models_client.plan("Rabe naka 3 vary", CTX)
    assert calls[0]["tool"] == "ask_clarification" and "TimeoutError" in calls[0]["args"]["question"]
