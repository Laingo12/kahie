# Day 1 — Meta Model API test

Run: 2026-10-29 09:39 · script: `eval/day1_test.py`

## 1. Models

| .env | ID | Listed |
|---|---|---|
| MODEL_PLANNER | `muse-spark-1.3` | ✓ |
| MODEL_EAR2 | `muse-spark-1.2` | ✓ |
| MODEL_VISION | `muse-spark-1.3` | ✓ |
| MODEL_TRANSCRIBE | `muse-voice-transcribe-1.0` | ✓ |

All IDs returned: `muse-image-1.0`, `muse-spark-1.1`, `muse-spark-1.2`, `muse-spark-1.2-contributor`, `muse-spark-1.3`, `muse-spark-1.3-contributor`, `muse-voice-transcribe-1.0`, `muse-wonder-1.0`, `sam-3.1`

## 2. Planner (Spark tools) on reference text

| Case | Note | Expected | Got | OK | ms |
|---|---|---|---|---|---|
| mg-001 | Rabe naka telo kapoaka vary, nandoa roa arivo Ariary. | 2 line(s) | [{"tool": "record_sale", "args": {"customer": "Rabe", "items": [{"product": "vary", "qty": 3.0, "unit": "kapoaka", "unit_price": null}], "paid": 2000}}] | ✓ | 3146 |
| mx-002 | Rabe naka trois kapoaka vary, crédit daholo. | 2 line(s) | [{"tool": "record_sale", "args": {"customer": "Rabe", "items": [{"product": "vary", "qty": 3, "unit": "kapoaka", "unit_price": null}], "paid": 0}}] | ✓ | 2971 |
| mg-018 | Bodo naka zavatra. | ask line(s) | [{"tool": "ask_clarification", "args": {"question": "Inona no nalain'i Bodo, ohatra firy ary ohatrinona?"}}] | ✓ | 2881 |

## 3. Ears on Malagasy recordings

No recordings in `eval/audio/` (name them `<id>.m4a`, e.g. `mg-001.m4a`). Ears not tested yet.

## Notes

- Recordings were made with written consent; speakers know the audio is sent to the Meta Model API.
- Free credits: content may be used by Meta for product improvement (Billing page).
