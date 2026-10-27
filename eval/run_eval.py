"""Evaluation harness — runs every voice note through the agent and scores the planned actions.

Usage:
    python eval/run_eval.py --cases eval/cases.jsonl --config A
Configs (see README): A = ear 1 only, B = ear 1 + lexicon, C = two ears + lexicon, D = C on open weights.

Each case (one JSON per line):
    {"id": "mg-001", "audio": "eval/audio/mg-001.webm", "lang": "mg",
     "reference": "Rabe naka 3 kapoaka vary...", "expected": [{"kind": "sale", "product": "vary", "qty": 3, ...}]}
"""
import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

CONFIG_ENV = {
    "A": {"KAHIE_EAR2": "off", "KAHIE_LEXICON": "off"},
    "B": {"KAHIE_EAR2": "off", "KAHIE_LEXICON": "on"},
    "C": {"KAHIE_EAR2": "on", "KAHIE_LEXICON": "on"},
    "D": {"KAHIE_EAR2": "on", "KAHIE_LEXICON": "on", "KAHIE_BACKEND": "open"},
}
CRITICAL = ("product", "qty", "customer", "amount", "total", "delta")


def _key(line):
    def norm(v):
        return int(v) if isinstance(v, float) and v.is_integer() else v
    return {k: norm(line.get(k)) for k in ("kind",) + CRITICAL if line.get(k) is not None}


def _audio_for(case) -> bytes:
    """The recording for a case: the "audio" path if given, else eval/audio/<id>.<any extension>
    (phone recorders give .m4a, .ogg, .wav… ffmpeg converts them all)."""
    if case.get("audio") and (ROOT / case["audio"]).exists():
        return (ROOT / case["audio"]).read_bytes()
    found = sorted((ROOT / "eval" / "audio").glob(case["id"] + ".*"))
    return found[0].read_bytes() if found else b""


def score(expected, got):
    exp = sorted(json.dumps(_key(x), sort_keys=True) for x in expected)
    out = sorted(json.dumps(_key(x), sort_keys=True) for x in got)
    exact = exp == out
    critical = (not exact) and bool(got)  # something was proposed, and it is wrong
    return exact, critical


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(ROOT / "eval" / ("cases.jsonl" if (ROOT / "eval" / "cases.jsonl").exists() else "cases.example.jsonl")))
    ap.add_argument("--config", default="C", choices=list(CONFIG_ENV))
    ap.add_argument("--out", default=None)
    ap.add_argument("--text-only", action="store_true", help="ignore audio, feed the reference text (tests planner + verifier only)")
    args = ap.parse_args()

    os.environ.update(CONFIG_ENV[args.config])
    os.environ["KAHIE_DB"] = str(Path(tempfile.mkdtemp()) / "eval.db")
    from app import db
    from app.agent import pipeline

    rows = []
    for line in Path(args.cases).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        conn = db.connect()
        db.init(conn)
        db.seed_demo(conn)
        audio = b"" if args.text_only else _audio_for(case)
        t0 = time.perf_counter()
        res = pipeline.handle_voice(conn, audio, "audio/webm", hint_text=None if audio else case["reference"])
        ms = (time.perf_counter() - t0) * 1000
        v = res["verdict"]
        exact, critical = score(case["expected"], v.get("lines", []))
        rows.append({"id": case["id"], "lang": case.get("lang"), "audio": bool(audio), "level": v["level"], "exact": exact,
                     "critical": critical, "clarified": v["level"] == "clarify", "ms": round(ms)})
        conn.execute("DELETE FROM pending_plans")

    n = len(rows)
    summary = {
        "config": args.config, "n": n, "with_audio": sum(r["audio"] for r in rows),
        "exact_rate": round(sum(r["exact"] for r in rows) / n, 3) if n else None,
        "critical_errors": sum(r["critical"] for r in rows),
        "clarifications": sum(r["clarified"] for r in rows),
        "median_ms": statistics.median(r["ms"] for r in rows) if n else None,
        "by_lang": {lg: round(sum(r["exact"] for r in rows if r["lang"] == lg) / max(1, sum(r["lang"] == lg for r in rows)), 3)
                    for lg in sorted({r["lang"] for r in rows})},
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
