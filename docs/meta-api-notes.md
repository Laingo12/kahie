# Meta Model API — what the docs say, and what J1 must test

Checked 2026-10-26 against the public docs and integrations. "Docs" ≠ "tested": every line marked **J1** needs a real call with our key.

## 1. Voice Transcribe (ear 1)

| Point | What the docs say | J1 test |
| --- | --- | --- |
| Endpoint | Realtime WebSocket `wss://api.meta.ai/v1/asr/realtime`, Bearer key, mono PCM16 audio | Connect, send one note |
| Batch / REST | Listed as not supported in the LiveKit plugin; OpenRouter lists a synchronous variant | Check whether a non-streaming call exists |
| Malagasy | Not among the 25 bias languages. Rejection is not documented; an unmapped language "degrades to auto-detection" | 10 Malagasy notes: (a) no `language_bias`, (b) `language_bias=["French"]` — compare |
| Prompt / context | No `context` parameter documented | — |
| Modes | `ENDPOINTING`, `PUSH_TO_TALK`, `DIARIZATION` | Use `PUSH_TO_TALK` (we send a finished note) |

## 2. Spark with audio (ear 2)

| Point | What the docs say | Code |
| --- | --- | --- |
| Audio input | Yes: `input_audio` part `{data: <base64>, format: "wav"}` on Chat Completions and Responses | done |
| Formats | MP3 and WAV only | webm → WAV with ffmpeg (`to_wav`) |
| Model | Use **muse-spark-1.2**: audio on 1.3 "currently not fully supported" | `MODEL_EAR2=muse-spark-1.2` |
| Contributor tier | `input_audio` silently dropped on `muse-spark-1.2-contributor` (HTTP 200, no error) — cookbook issue #59, open | token-count guard; avoid contributor tiers for ear 2 |
| Output length | Set `max_tokens` ≥ 4000 or the transcript can come back empty | done |

If ear 2 fails on J1: fallback double listening = ear 1 + Spark re-reading the ear-1 text with the lexicon (text-only second opinion).

## 3. Keyword biasing (lexicon)

| Point | What the docs say | Code |
| --- | --- | --- |
| Keywords | `keywords: list[str]` at handshake; raises odds of recognition, does not guarantee spelling | `lexicon.keywords()` → ear 1 |
| Update | Cannot change on an active stream | one stream per note, so each note gets the latest lexicon |
| Backup | — | lexicon also in Spark prompts + `lexicon.normalize()` after transcription |

## Still to confirm

- Exact planner model ID (`client.models.list()`).
- Native WebSocket message names for Voice Transcribe (copy from the pipecat / LiveKit Meta STT source).
- `AUDIO_MIN_TOKENS` threshold in `models_client.py`, calibrated with one real note.

Sources: dev.meta.ai/docs/video-understanding · docs.pipecat.ai (Meta STT) · docs.livekit.io (Meta STT plugin) · github.com/meta-models/meta-model-cookbook/issues/59
