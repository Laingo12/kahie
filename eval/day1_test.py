"""Day-1 go/no-go test against the real Meta Model API. Writes docs/day1.md.

    python eval/day1_test.py                 # up to 10 recordings found in eval/audio/
    python eval/day1_test.py --max 5         # fewer notes (cheaper)
    python eval/day1_test.py --no-audio      # only models list + planner checks

What it checks
  1. The API key works and which model IDs exist (vs. the IDs in .env).
  2. The planner (Spark tools) on 3 reference notes: a sale with a debt, "crédit daholo", a note that must be asked.
  3. For each recording eval/audio/<id>.<ext> (Malagasy first):
       ear 1a  Voice Transcribe, no language bias (auto-detect)
       ear 1b  Voice Transcribe, languageBias = ["French"]
       ear 2   Spark audio (MODEL_EAR2) with the lexicon
     scored against the reference text: word error rate, and whether the NUMBERS are right
     (the thing that matters for a ledger), after the same normalisation the app uses.

Cost: about 4 API calls per recording + 3 planner calls. Check the Usage page afterwards.
Uses a throw-away database: your data/kahie.db is not touched.
"""
import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["KAHIE_DB"] = str(Path(tempfile.mkdtemp()) / "day1.db")

from app import config, db, lexicon, models_client  # noqa: E402
from app.agent import pipeline, verifier  # noqa: E402
from app.numbers_mg import numbers_in, to_digits  # noqa: E402

PLANNER_CASES = ["mg-001", "mx-002", "mg-018"]


def words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", to_digits(text or "").lower())


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / len(r)


def fresh_conn():
    conn = db.connect(Path(tempfile.mkdtemp()) / "d.db")
    db.init(conn)
    db.seed_demo(conn)
    return conn


def timed(fn, *a, **k):
    t = time.perf_counter()
    out = fn(*a, **k)
    return out, round((time.perf_counter() - t) * 1000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "day1.md"))
    args = ap.parse_args()

    if not config.MODEL_API_KEY or config.BACKEND == "mock":
        sys.exit("MODEL_API_KEY missing or KAHIE_BACKEND=mock in .env — set the key and leave KAHIE_BACKEND empty.")

    cases = {c["id"]: c for c in map(json.loads, (ROOT / "eval" / "cases.jsonl").read_text(encoding="utf-8").splitlines()) if c}
    md = [f"# Day 1 — Meta Model API test", "", f"Run: {datetime.now():%Y-%m-%d %H:%M} · script: `eval/day1_test.py`", ""]

    # 1. models -------------------------------------------------------------------
    print("1. models.list …")
    try:
        ids = sorted(m.id for m in models_client._client().models.list())
    except Exception as e:
        sys.exit(f"API call failed: {type(e).__name__}: {e}")
    wanted = {"MODEL_PLANNER": config.MODEL_PLANNER, "MODEL_EAR2": config.MODEL_EAR2,
              "MODEL_VISION": config.MODEL_VISION, "MODEL_TRANSCRIBE": config.MODEL_TRANSCRIBE}
    md += ["## 1. Models", "", "| .env | ID | Listed |", "|---|---|---|"]
    md += [f"| {k} | `{v}` | {'✓' if v in ids else '✗ not listed'} |" for k, v in wanted.items()]
    md += ["", "All IDs returned: " + ", ".join(f"`{i}`" for i in ids), ""]
    print("   ", ids)

    # 2. planner ------------------------------------------------------------------
    print("2. planner …")
    md += ["## 2. Planner (Spark tools) on reference text", "", "| Case | Note | Expected | Got | OK | ms |", "|---|---|---|---|---|---|"]
    for cid in PLANNER_CASES:
        c = cases[cid]
        conn = fresh_conn()
        ctx = pipeline._context(conn)
        try:
            calls, ms = timed(models_client.plan, pipeline.normalize_ear(conn, c["reference"]), ctx)
            v = verifier.verify(conn, calls)
            got = [] if v["level"] == "clarify" else [
                {k: x[k] for k in ("kind", "product", "qty", "customer", "amount", "total", "delta") if x.get(k) is not None}
                for x in v["lines"]]
            exp = [{k: x[k] for k in ("kind", "product", "qty", "customer", "amount", "total", "delta") if x.get(k) is not None}
                   for x in c["expected"]]
            norm = lambda L: sorted(json.dumps({k: (int(v) if isinstance(v, float) and v.is_integer() else v) for k, v in x.items()}, sort_keys=True) for x in L)
            ok = norm(got) == norm(exp)
            md.append(f"| {cid} | {c['reference']} | {len(exp) or 'ask'} line(s) | {json.dumps(calls, ensure_ascii=False)[:160]} | {'✓' if ok else '✗'} | {ms} |")
        except Exception as e:
            md.append(f"| {cid} | {c['reference']} | | **{type(e).__name__}: {str(e)[:120]}** | ✗ | |")
    md.append("")

    # 3. ears ---------------------------------------------------------------------
    audio_dir = ROOT / "eval" / "audio"
    found = [] if args.no_audio else sorted(
        (p for p in audio_dir.glob("*.*") if p.stem in cases),
        key=lambda p: ({"mg": 0, "mix": 1, "fr": 2}[cases[p.stem]["lang"]], p.stem))[: args.max]
    md += ["## 3. Ears on Malagasy recordings", ""]
    if not found:
        md += ["No recordings in `eval/audio/` (name them `<id>.m4a`, e.g. `mg-001.m4a`). Ears not tested yet.", ""]
    else:
        conn = fresh_conn()
        kw = lexicon.keywords(conn)
        lex_prompt = lexicon.as_prompt(conn)
        ears = {"VT auto": lambda a: models_client.voice_transcribe(a, kw, None),
                "VT French": lambda a: models_client.voice_transcribe(a, kw, ["French"]),
                f"Spark {config.MODEL_EAR2}": lambda a: models_client.listen_with_spark(a, "audio/webm", lex_prompt)}
        stats = {name: {"wer": [], "num_ok": 0, "ms": [], "errors": 0} for name in ears}
        rows = []
        for p in found:
            c, audio = cases[p.stem], p.read_bytes()
            ref_nums = numbers_in(pipeline.normalize_ear(conn, c["reference"]))
            print(f"3. {p.name} …")
            row = {"id": p.stem, "ref": c["reference"], "ears": {}}
            for name, fn in ears.items():
                out, ms = timed(fn, audio)
                text = out.get("text", "")
                hyp = pipeline.normalize_ear(conn, text)
                s = stats[name]
                if out.get("error") or out.get("dropped"):
                    s["errors"] += 1
                w = wer(c["reference"], text)
                nums_ok = numbers_in(hyp) == ref_nums
                s["wer"].append(w); s["ms"].append(ms); s["num_ok"] += nums_ok
                row["ears"][name] = {"text": text, "wer": w, "nums_ok": nums_ok, "ms": ms,
                                     "error": out.get("error") or ("audio dropped" if out.get("dropped") else None)}
            rows.append(row)
        n = len(found)
        md += [f"{n} recordings. *Numbers OK* = every amount and quantity right after normalisation (what the ledger needs).", "",
               "| Ear | Numbers OK | Mean WER | Median ms | Errors |", "|---|---|---|---|---|"]
        for name, s in stats.items():
            md.append(f"| {name} | {s['num_ok']}/{n} | {statistics.mean(s['wer']):.0%} | {statistics.median(s['ms']):.0f} | {s['errors']} |")
        best = max(stats.items(), key=lambda kv: (kv[1]["num_ok"], -statistics.mean(kv[1]["wer"])))
        share = best[1]["num_ok"] / n
        verdict = ("**GO voice** — the voice loop is the demo." if share >= 0.7 else
                   "**GO with questions** — numbers are often wrong: the two-ear question is the core of the demo." if share >= 0.4 else
                   "**Switch input** — make typed text + notebook photo the main input, keep voice as experimental; document the gap.")
        md += ["", f"Best ear: **{best[0]}** ({best[1]['num_ok']}/{n} numbers OK). Suggested decision: {verdict}", "",
               "### Per note", ""]
        for r in rows:
            md.append(f"**{r['id']}** — ref: *{r['ref']}*  ")
            for name, e in r["ears"].items():
                flag = "✓" if e["nums_ok"] else "✗"
                extra = f" — ⚠ {e['error']}" if e["error"] else ""
                md.append(f"- {name}: {flag} WER {e['wer']:.0%} · {e['ms']} ms · “{e['text']}”{extra}")
            md.append("")

    md += ["## Notes", "", "- Recordings were made with written consent; speakers know the audio is sent to the Meta Model API.",
           "- Free credits: content may be used by Meta for product improvement (Billing page).", ""]
    Path(args.out).write_text("\n".join(md), encoding="utf-8")
    print(f"\nWritten: {args.out}")


if __name__ == "__main__":
    main()
