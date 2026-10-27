# Evaluation set

**60 scripted voice notes** in `cases.jsonl` — 30 Malagasy, 20 mixed Malagasy/French, 10 French —
including 6 notes where the right answer is to ask a question (`"expected": []`).
Read by at least 6 speakers, about a third of them with market background noise.

- `make_cases.py` holds the scripts and the ideal tool calls. The `expected` lines are produced by the real
  verifier on the demo shop, so prices, totals and debts always match Kahie. Edit a note there, then run
  `python eval/make_cases.py` to rebuild `cases.jsonl`.
- Customers are fictional (Rabe, Soa, Naivo, Bodo in the demo shop; Lalao and Hery are new customers).
- Recordings: `eval/audio/<id>.<ext>` — e.g. `eval/audio/mg-001.m4a`. Any format ffmpeg reads works.
  The folder is git-ignored: publish recordings only if the speakers agreed in writing.
- Written consent from every speaker before recording (see the recording sheet).

Run all configurations and keep the JSON outputs for the README chart:

```bash
for c in A B C D; do python eval/run_eval.py --config $c --out eval/results-$c.json; done
python eval/run_eval.py --config C --text-only     # reference text instead of audio: planner + verifier only
```

A case without a recording is fed its reference text; the summary reports `with_audio` so a run with
missing recordings is visible. A *critical error* is a wrong line proposed for writing (wrong product,
quantity, customer, amount, total or stock change).
