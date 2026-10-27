# Kahie — the voice notebook that understands Malagasy

> **Kahie turns a shopkeeper's voice notes in Malagasy into a verified ledger — and the ledger into a credit history.**

Built for the **Meta Global AI Developer Hackathon** (Oct 26 – Nov 4, 2026) · Indie Developer track.
All code in this repository was written during the hackathon (see commit history).

---

## The problem

In Madagascar, small shopkeepers record sales and debts (*trosa*) in a paper notebook — a *kahie* — when they have time.
Debts get forgotten, notebooks wear out, and without a reliable written record there is no access to microcredit.
Existing tools expect typing in French or English.

## What Kahie does

1. The shopkeeper **speaks** a note in Malagasy, French or both:
   *"Rabe naka 3 kapoaka vary, nandoa 2000 Ar"* (Rabe took 3 cups of rice, paid 2,000 Ar).
2. **Two ears listen**: Muse Voice Transcribe and Muse Spark (raw audio). Both readings are normalised first (spoken Malagasy numbers such as *roa amby telopolo* → 32, learned words from the lexicon). If they still disagree on an amount or a name, Kahie asks a short question instead of guessing, and the shopkeeper answers with one tap on the right reading.
3. The agent **plans** tool calls (sale, debt, stock), the **verifier** checks them against the database.
4. A **one-tap confirmation card** shows exactly what will be written. Nothing is written before the tap.
5. The executor writes everything in one transaction and appends a row to a **hash-chained audit log** (undo always available).
6. Every correction **teaches the lexicon** (product names, customer nicknames, local units like *kapoaka*), which is fed back into ASR biasing and the planner prompt.
7. A **weekly statement** is sealed with the head of the hash chain, so anyone can check it has not been edited.

Onboarding: take a **photo of the existing paper notebook**; Spark vision imports the open debts for confirmation.

## Architecture

```
 Voice note ──┬─► Ear 1 · Muse Voice Transcribe ─┐
              └─► Ear 2 · Muse Spark (audio)    ─┴─► Reconciler ──► Planner (Spark tools) ──► Verifier
 Notebook photo ──► Spark vision ───────────────────────────────────────┘                       │
                     ▲                                                                           ▼
                  Lexicon ◄──────────── every correction ◄──────────── Confirmation card (1 tap)
                                                                                                 │
                         Dashboard · Sealed statement · Debt reminder ◄── Executor + hash-chained audit_log
```

| Meta model | Role in Kahie |
| --- | --- |
| Muse Voice Transcribe | Ear 1 — fast streaming transcription with keyword/context biasing |
| Muse Spark (audio input) | Ear 2 — listens to the raw audio with the merchant lexicon |
| Muse Spark (tools) | Planner — turns the note into structured tool calls |
| Muse Spark (vision) | Reads the paper notebook photo |
| Muse Spark Open | Same agent on open weights (evaluation config D) |

## Evaluation

60 voice notes (30 Malagasy, 20 mixed, 10 French), 6+ consenting speakers, some with market noise.
Malagasy is **not** among the 25 languages validated at Voice Transcribe launch — measuring how far two ears and a learning lexicon close that gap is the core result.

| Config | Transcription | Reasoning | Exact actions | Critical errors | Median latency |
| --- | --- | --- | --- | --- | --- |
| 0 | reference text (no ASR) | rules only, no model | 15 % | 1 | ~1 ms |
| A | Voice Transcribe only | Spark | _tbd_ | _tbd_ | _tbd_ |
| B | + lexicon biasing | Spark | _tbd_ | _tbd_ | _tbd_ |
| C | Two ears + lexicon | Spark | _tbd_ | _tbd_ | _tbd_ |
| D | Two ears + lexicon | Spark Open | _tbd_ | _tbd_ | _tbd_ |

Row 0 is a rule-based baseline on the 60 scripted notes (`--text-only`): it asks a question on 56 of 60 and still writes one wrong debt ("crédit daholo" read as paid) — the gap the Meta models have to close.

*Critical error* = a wrong amount, product or customer proposed for writing. Results are published as measured, favourable or not. See [`eval/`](eval/).

## Run it

```bash
git clone <this repo> && cd kahie
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# ffmpeg is required for real voice notes (browser webm → WAV/PCM16): apt install ffmpeg / brew install ffmpeg
cp .env.example .env            # add MODEL_API_KEY; without a key Kahie runs in offline "mock" mode
uvicorn app.main:app --app-dir backend --reload
# open http://localhost:8000 on your phone (same network) or desktop
```

Tests and evaluation:

```bash
python -m pytest -q
python eval/run_eval.py --config C
```

## Repository layout

```
kahie/
├── backend/app/
│   ├── main.py            FastAPI: /api/voice /image /clarify /confirm /undo /lexicon /report /statement /verify
│   ├── models_client.py   Meta Model API wrapper (ear 1, ear 2, planner, vision) + offline mock
│   ├── lexicon.py         learned merchant vocabulary → ASR biasing + prompt + normalisation
│   ├── numbers_mg.py      spoken Malagasy/French numbers → digits (roa amby telopolo → 32)
│   ├── audit.py           hash-chained audit log, verify()
│   ├── db.py, schema.sql  SQLite: products, customers, sales, debts, stock_moves, lexicon, audit_log
│   └── agent/
│       ├── pipeline.py    listen → normalise each ear → reconcile → plan → verify → pending; answers to questions
│       ├── reconciler.py  double listening: compare numbers and names
│       ├── verifier.py    rules: unknown product, stock, unusual price, missing customer
│       ├── executor.py    one transaction + audit row + undo
│       └── tools.json     tool schemas given to Spark
├── frontend/              PWA: record, confirmation card, dashboard, statement, offline queue
├── eval/                  run_eval.py, case format, configs A–D
├── docs/meta-api-notes.md what to confirm against the Meta docs
└── tests/                 end-to-end API tests + numbers, ears and clarification tests (mock mode)
```

## Safety and privacy

- No write without explicit confirmation; every write is undoable and logged.
- The API key lives only in `.env`; a spend limit is set on the Meta billing page.
- The demo shop and evaluation set contain no real customer data. Voice recordings are used only with written consent.
- The sealed statement is a prototype of verifiable records, not a partnership with any financial institution.

## Team

<!-- list every contributor exactly as on the submission form -->
- _Name_ — role

## License

MIT — see [LICENSE](LICENSE).
