# System Review Reference

Quick reference for this project's technology stack and design decisions.

---

## Stack Overview

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.11 |
| Web framework | FastAPI + Uvicorn (ASGI) |
| Telephony | Twilio Media Streams (WebSocket, 8kHz µ-law G.711) |
| AI model | OpenAI Realtime API — `gpt-realtime-1.5` (speech-to-speech) |
| Tunnel | ngrok (HTTPS + WSS to expose localhost) |
| Audio processing | `audioop` (stdlib, µ-law ↔ PCM16), `pydub` + FFmpeg (MP3 export) |
| WebSocket client | `websockets` library (connects to OpenAI) |
| Async model | `asyncio` — two concurrent tasks per call (Twilio→OpenAI, OpenAI→Twilio) |

## Key APIs

| API | Protocol | Purpose |
|-----|----------|---------|
| **OpenAI Realtime API** | WebSocket (`wss://api.openai.com/v1/realtime`) | Speech-to-speech model — receives audio, generates audio responses with transcripts |
| **Twilio Media Streams** | WebSocket (bidirectional) | Bridges PSTN phone audio to our server as 8kHz µ-law chunks |
| **Twilio REST API** | HTTPS | Initiates outbound calls, force-hangup on timeout |
| **ngrok** | HTTP tunnel | Exposes local FastAPI server to the internet for Twilio webhooks |

## LLM & Model Details

- **Model**: `gpt-realtime-1.5` (OpenAI GA Realtime API)
- **Modality**: audio-in / audio-out (no text intermediate step)
- **Audio format**: `audio/pcmu` (G.711 µ-law, 8kHz) on both input and output
- **VAD**: `server_vad` — server-side voice activity detection for turn-taking
- **Transcription**: `whisper-1` for input (agent speech), model-native for output (bot speech)
- **Session type**: `realtime` (GA protocol, no beta header)

## Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| µ-law on both sides | Eliminates all real-time resampling. Twilio and OpenAI both support `audio/pcmu` natively — zero conversion in the relay path |
| Deferred greeting | Bot waits for first agent audio before responding. Prevents speaking into silence before the agent picks up |
| `asyncio.wait(FIRST_COMPLETED)` | When Twilio stream stops, the OpenAI task is immediately cancelled. No stale queued responses after call ends |
| `audioop` over `pydub` for real-time | `audioop` is a C stdlib extension — microsecond latency per chunk. pydub/FFmpeg has ~50-100ms per-invocation overhead |
| Silence-padded recording | Wall-clock timestamps on each audio chunk; gaps >100ms get proportional silence inserted for natural playback |
| Module-level `CallState` singleton | One call at a time by design — personas run sequentially |

## Data Flow (per call)

```
main.py → ngrok → FastAPI server
                  ↓
          Twilio dials target number
          Twilio connects WebSocket to /stream
                  ↓
          Server connects to OpenAI Realtime WebSocket
                  ↓
          [Twilio µ-law] ←→ [Server relay] ←→ [OpenAI µ-law]
                  ↓
          AudioRecorder taps both directions
                  ↓
          On call end → save .mp3 (stereo) + .txt (transcript)
```

## Persona System

10 test personas defined in `src/personas/prompts.json`, each with a unique behavioral profile (polite scheduler, impatient interrupter, rambling elder, etc.). Selected via `--persona` CLI flag. The system prompt is injected into the OpenAI session instructions with an English-language constraint prepended.

## Error Handling Highlights

- 3 retries on Twilio call initiation (30s apart)
- 3 reconnect attempts for OpenAI WebSocket drops (exponential backoff)
- 5-minute per-call timeout with forced hangup via Twilio API
- `response_cancel_not_active` errors suppressed (benign barge-in artifact)
- `CancelledError` handled gracefully in all async tasks
- Partial artifacts saved on any mid-call failure
