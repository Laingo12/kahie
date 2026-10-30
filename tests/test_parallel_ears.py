"""The two ears are called at the same time: a note waits for the slower ear, not for both in a row."""
import tempfile
import time
from pathlib import Path

from app import db, models_client
from app.agent import pipeline


def test_ears_listen_in_parallel(monkeypatch):
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db"); db.init(conn); db.seed_demo(conn)

    def slow(text):
        def ear(*args, **kwargs):
            time.sleep(0.4)
            return {"text": text, "source": "test"}
        return ear

    monkeypatch.setattr(models_client, "transcribe", slow("Rabe naka telo kapoaka vary"))
    monkeypatch.setattr(models_client, "listen_with_spark", slow("Rabe naka telo kapoaka vary"))
    t = time.perf_counter()
    r = pipeline.handle_voice(conn, b"x", "audio/webm")
    assert time.perf_counter() - t < 0.75          # sequential would take ≥ 0.8 s
    assert r["ears"] == {"ear1": "Rabe naka telo kapoaka vary", "ear2": "Rabe naka telo kapoaka vary"}
