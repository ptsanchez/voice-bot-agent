# Architecture & Usage Guide

This document explains how the Voice Bot Agent works end-to-end. It is written for someone with no prior knowledge of the codebase.

---

## 1. What This Project Does

The Voice Bot Agent is an automated QA testing tool that simulates realistic patient phone calls to a target medical voice agent. It:

1. Dials the target phone number using Twilio
2. Streams live audio between Twilio and the **OpenAI Realtime API** (a speech-to-speech AI model)
3. The AI model plays the role of a patient based on a **persona script** (e.g., "book a routine physical", "demand a Sunday appointment")
4. Records both sides of the conversation and saves them as an MP3 file and a text transcript

The system runs as a single command: `python src/main.py --persona <persona_key>`.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  [Target Voice Agent] ←── PSTN phone network ──→ [Twilio]          │
│       (the system under test)                         │             │
│                                                        │             │
│                                          8kHz µ-law audio            │
│                                        over WebSocket (wss://)       │
│                                                        │             │
│                                                        ▼             │
│  [FastAPI Server] ←── ngrok HTTPS tunnel ─── [ngrok Public URL]     │
│   (runs locally on port 8765)                          │             │
│       │                                                │             │
│       ├── /twiml   → returns TwiML instructions to Twilio            │
│       ├── /stream  → bidirectional audio WebSocket relay             │
│       └── /status  → receives call completion webhooks               │
│              │                                                       │
│              │                 8kHz µ-law audio                       │
│              │                over WebSocket (wss://)                 │
│              ▼                                                       │
│  [OpenAI Realtime API]                                                │
│   (gpt-realtime-1.5, voice=alloy)                                    │
│                                                                       │
│  [Audio Recorder] ←── taps both audio directions                     │
│       ├── saves mixed stereo .mp3 to conversations/                  │
│       └── saves timed .txt transcript to conversations/              │
└─────────────────────────────────────────────────────────────────────┘
```

### Data Flow Summary

| Step | What Happens |
|------|-------------|
| 1 | You run `python src/main.py --persona weekend_warrior` |
| 2 | The script reads the persona's system prompt from `prompts.json` |
| 3 | It starts an **ngrok tunnel** to expose localhost:8765 to the internet |
| 4 | It starts a **FastAPI server** on port 8765 |
| 5 | It calls the **Twilio API** to place an outbound call to the target agent number |
| 6 | Twilio answers → fetches TwiML from `https://ngrok-url/twiml` → connects a **Media Stream** WebSocket to `wss://ngrok-url/stream` |
| 7 | The server connects to **OpenAI Realtime API** WebSocket with the persona's instructions |
| 8 | The bot waits for the agent to speak, then responds naturally |
| 9 | Audio flows bidirectionally: Twilio ↔ Server ↔ OpenAI for the duration of the call |
| 10 | When the call ends (either side hangs up, or 5-min timeout), the server saves the MP3 and transcript |
| 11 | The script shuts down ngrok and the server |

---

## 3. Prerequisites

### System Dependencies

- **Python 3.10+**
- **FFmpeg** — required by `pydub` for exporting MP3 files. NOT a pip package; install separately:
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`

### Accounts

| Service | What You Need |
|---------|--------------|
| **OpenAI** | API key with Realtime API access |
| **Twilio** | Account SID, Auth Token, and a purchased phone number (E.164 format, e.g., `+12223334444`) |
| **ngrok** | Auth token (free tier works) |

### Environment Variables

Copy `.env.example` to `.env` and fill in:

```
OPENAI_API_KEY=sk-...                  # OpenAI API key
OPENAI_MODEL=gpt-realtime-1.5          # Realtime model
TWILIO_ACCOUNT_SID=AC...               # Twilio account SID
TWILIO_AUTH_TOKEN=...                   # Twilio auth token
TWILIO_PHONE_NUMBER=+15625696327        # Your purchased Twilio number
AGENT_PHONE_NUMBER=+18054398008         # Target voice agent number
NGROK_AUTHTOKEN=...                     # ngrok authtoken
```

---

## 4. How to Run

```bash
# Install Python dependencies
pip install -r requirements.txt

# Run any persona
python src/main.py --persona straightforward_scheduler
python src/main.py --persona weekend_warrior
python src/main.py --persona chronic_interrupter
# ... see full list below
```

### What You'll See on Screen

```
============================================================
Persona: Weekend Warrior (weekend_warrior)
Description: Insists on Sunday booking to catch calendar logic bugs.
============================================================

Starting ngrok tunnel...
INFO     ngrok tunnel opened: https://abc123.ngrok.io
INFO     FastAPI app initialized
Twiml URL: https://abc123.ngrok.io/twiml
Status callback URL: https://abc123.ngrok.io/status
Initiating call to +18054398008...
Call initiated: SID=CAabc123...
Waiting for call to complete (timeout: 300s)...
INFO     Call initiated (1/3): SID=CAabc123...
INFO     Status webhook: SID=CAabc123 status=completed

Call duration: 87.3s
Saving artifacts...
  Audio: conversations/2026-06-23T13-30-00_weekend_warrior.mp3
  Transcript: conversations/2026-06-23T13-30-00_weekend_warrior.txt
Done.
```

---

## 5. What Gets Saved

Each call produces two files in the `conversations/` directory:

### MP3 File

`2026-06-23T13-30-00_weekend_warrior.mp3`

- **Stereo audio**: left channel = Agent (the simulated patient / bot), right channel = Target Voice Agent (the system under test)
- 64kbps MP3 format
- Both channels are time-aligned with silence padding for natural playback
- Silence gaps between responses are preserved based on real-time chunk timestamps

### Transcript File

`2026-06-23T13-30-00_weekend_warrior.txt`

```
[00:00] Bot: Hello, thanks for calling. How can I help you today?
[00:02] Agent: Hi, I need to schedule an appointment. I can only come in on Sunday morning at 10 AM.
[00:05] Bot: I see. Let me check our availability. Unfortunately, we're not open on Sundays.
[00:08] Agent: Really? There's no way at all? My work schedule is Monday through Saturday and I absolutely cannot take time off.
[00:14] Bot: I understand. Let me see what we have available on Saturday...
```

- `[MM:SS]` = relative timestamp from the start of the call
- `[Agent]` = the simulated patient (OpenAI Realtime API)
- `[Bot]` = the target voice agent (transcribed from audio)

---

## 6. Component Deep-Dive

### 6a. `src/main.py` — CLI Entry Point & Orchestrator

This is the script you run. It does everything in sequence:

1. Parses `--persona` argument
2. Loads the matching persona from `src/personas/prompts.json`
3. Prepends an English-language constraint to the persona prompt
4. Reads environment variables from `.env`
5. Creates an `AudioRecorder` instance (the artifact collector)
6. Starts an **ngrok tunnel** to expose the local server to the internet
7. Creates a **uvicorn** server running the FastAPI app from `server.py`
8. Calls the **Twilio API** to place an outbound call, pointing Twilio's webhooks to the ngrok URLs
9. Waits for the call to complete (using `asyncio.Event`)
10. Saves the MP3 and transcript files using `run_in_executor` (runs CPU-heavy pydub work in a thread pool)
11. Shuts down the server and ngrok tunnel

Key design choices:
- **Single call at a time**: The `CallState` object in `server.py` is a module-level singleton. Only one call can be in progress at any time. This is intentional since personas run sequentially.
- **5-minute safety net**: If the call doesn't complete naturally (both sides hang up), the wait times out and forces hangup via Twilio API.

### 6b. `src/server.py` — FastAPI Web Server

Three endpoints:

**`GET /twiml`** — Returns TwiML (Twilio's XML instruction language):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://abc123.ngrok.io/stream"/>
  </Connect>
</Response>
```
This tells Twilio: "When the call connects, stream all audio to this WebSocket URL." Note the `wss://` (WebSocket Secure) — Twilio requires encryption.

**`WebSocket /stream`** — The heart of the system. This is a bidirectional relay:

1. Waits for Twilio's `start` event (contains a `streamSid`)
2. Connects to **OpenAI Realtime API** WebSocket
3. Sends a `session.update` with the persona's system prompt, voice config, and transcription settings
4. Spawns **two concurrent tasks** with `asyncio.wait(FIRST_COMPLETED)`:
   - `twilio_to_openai()`: receives 8kHz µ-law audio from Twilio, forwards directly to OpenAI + records to AudioRecorder
   - `openai_to_twilio()`: receives events from OpenAI (audio chunks, transcripts, speech events), forwards µ-law audio directly to Twilio + records
5. **Deferred greeting**: The bot does not speak until the first audio chunk arrives from the agent, ensuring it responds to the agent's greeting rather than speaking into silence
6. When either task completes (Twilio `stop` event or OpenAI disconnect), the other task is **immediately cancelled** — no stale responses are sent after the call ends
7. On disconnect: cleans up OpenAI connection, cancels timeout, signals completion

Barge-in handling: When OpenAI sends `input_audio_buffer.speech_started` (the Agent heard the target voice agent start speaking), the server sends a `response.cancel` to OpenAI to stop its audio output — allowing natural turn-taking. Cancel-not-active errors are suppressed since they're expected when the user interrupts during silence.

**`POST /status`** — Receives call status webhooks from Twilio:
- `completed`: call finished normally → signals completion
- `failed`, `busy`, `no-answer`: call didn't connect → signals completion
- The `CallState.done` Event is `.set()`, which unblocks `main.py`'s wait

**`CallState`** — A shared state object that `main.py` populates before starting the server:
- `persona_prompt`: the system prompt from `prompts.json`
- `ngrok_url`: the public ngrok URL (used to build TwiML wss:// URL)
- `recorder`: the `AudioRecorder` instance
- `done`: an `asyncio.Event` that `main.py` waits on and `/status` or `/stream` sets
- `call_sid`: the Twilio call SID (used to force hangup on timeout)
- `call_completed`: flag to prevent double-completion

**`_schedule_timeout()` / `_cancel_timeout()`**: Sets a 300-second timer. If the call hasn't completed by then, it forces completion so the process doesn't hang forever.

### 6c. `src/openai_realtime.py` — OpenAI Realtime API Client

Wraps a WebSocket connection to `wss://api.openai.com/v1/realtime`.

**Connection**: Uses `websockets` library with `Authorization: Bearer {key}` header. Connects via query parameter `?model={model_name}`.

**Session Configuration** (sent as first event after connect):
```json
{
  "type": "session.update",
  "session": {
    "type": "realtime",
    "output_modalities": ["audio"],
    "instructions": "You are a polite patient...",
    "audio": {
      "input": {
        "format": { "type": "audio/pcmu" },
        "turn_detection": { "type": "server_vad" },
        "transcription": { "model": "whisper-1" }
      },
      "output": {
        "format": { "type": "audio/pcmu" },
        "voice": "alloy"
      }
    }
  }
}
```

Key settings:
- `audio.input.transcription.model: "whisper-1"` — enables agent speech transcripts
- `audio.input/output.format: "audio/pcmu"` — uses µ-law encoding matching Twilio's native format, eliminating all resampling
- `turn_detection.type: "server_vad"` — server-side voice activity detection for natural turn-taking

**Methods**:
- `send_audio(ulaw_base64)` — sends `input_audio_buffer.append` event
- `send_response_create(instructions_override)` — triggers the model to speak. Used after the first agent audio to trigger the initial greeting
- `cancel_response()` — sends `response.cancel` for barge-in handling
- `receive_events()` — async generator that yields events from OpenAI

**Reconnection**: If the WebSocket drops, `reconnect()` will retry up to 3 times with exponential backoff (2s, 4s, 6s delays).

### 6d. `src/audio_recorder.py` — Audio Recording & Format Conversion

**Audio Format Conversion** (two functions at module level):

| Function | What It Does | Why |
|----------|-------------|-----|
| `ulaw_to_pcm16()` | Converts G.711 µ-law bytes (8-bit) to linear PCM16 bytes (16-bit) | Recording stores PCM16 for pydub compatibility |
| `pcm16_to_ulaw()` | Converts PCM16 bytes to µ-law bytes | Available if needed for conversion |

Note: With the GA Realtime API using `audio/pcmu`, both Twilio and OpenAI exchange µ-law audio natively. Format conversion is only needed for the recording path (µ-law → PCM16 for pydub).

**AudioRecorder Class**:

A thread-safe buffer that accumulates audio from both directions with real-time silence padding:

- `add_agent_audio(mu_law_data)` — called when audio arrives from Twilio. Converts µ-law to PCM16, inserts silence for gaps >100ms between consecutive chunks, and stores it
- `add_bot_audio(pcm16_data)` — called when audio arrives from OpenAI (converted from µ-law). Inserts silence for gaps >100ms and stores PCM16
- `add_transcript(speaker, text)` — stores a timed transcript entry (`[00:12] Agent: Hi there`)
- `save_mp3(path)` — creates a stereo MP3 from the two buffers:
  1. Takes a snapshot of both buffers under the thread lock
  2. Creates `AudioSegment` objects (8kHz PCM16 mono)
  3. Pads the shorter one with silence so they have the same sample count
  4. Interleaves samples into stereo (Agent on left, Bot on right)
  5. Exports as 64kbps MP3
- `save_txt(path)` — writes the transcript to a text file

Silence preservation: Each `add_*_audio` call tracks the wall-clock timestamp. If the gap between consecutive chunks exceeds 100ms (a real pause vs. the normal ~20ms chunk interval), silence bytes are inserted proportional to the gap duration. This ensures the recording reflects natural conversation pacing.

Thread safety: Uses `threading.Lock` because `save_mp3`/`save_txt` run in a thread pool executor (they're CPU-bound pydub operations).

### 6e. `src/twilio_client.py` — Twilio API Client

Two operations:

**`initiate_call(twiml_url, status_callback_url)`**:
1. Calls `client.calls.create()` with:
   - `url` → the ngrok `/twiml` endpoint (TwiML instructions)
   - `to` → the target voice agent number
   - `from_` → the purchased Twilio number
   - `status_callback` → the ngrok `/status` endpoint
   - `status_callback_event` → completed, failed, busy, no-answer
   - `timeout` → 300 seconds (call setup timeout)
2. Retries up to **3 times** with **30-second delays** between attempts
3. Returns the call SID string

**`hangup_call(call_sid)`**: Calls `client.calls(sid).update(status="completed")` to force-hangup a call (used by the 5-minute timeout handler).

### 6f. `src/personas/prompts.json` — 10 Test Personas

Each persona has a `key` (used in `--persona` argument), a human-readable `name`, a `description`, and a `system_prompt` that gets injected into the OpenAI Realtime session as its instructions. The prompts instruct the model to:

- Embody a specific patient personality (e.g., polite, impatient, rambling)
- Speak naturally with appropriate pacing and fillers
- Follow a specific scenario arc (book → reschedule, demand Sunday, refuse to identify)
- Include explicit behavioral cues the agent can trigger

---

## 7. Audio Format Details

Both Twilio and the OpenAI Realtime API GA use **G.711 µ-law at 8kHz** natively when configured with `audio/pcmu`. This means no format conversion or resampling is needed in the real-time relay path.

| Property | Twilio | OpenAI (with `audio/pcmu`) |
|----------|--------|---------------------------|
| Encoding | G.711 µ-law (8-bit per sample) | G.711 µ-law (8-bit per sample) |
| Sample Rate | 8000 Hz | 8000 Hz |
| Channels | Mono | Mono |
| Bitrate | 64 kbps | 64 kbps |

**Twilio → OpenAI path**:
1. Receive base64-encoded µ-law chunk from Twilio WebSocket
2. Forward the base64 string directly to OpenAI as `input_audio_buffer.append`
3. Convert µ-law to PCM16 for recording only

**OpenAI → Twilio path**:
1. Receive base64-encoded µ-law delta from OpenAI `response.output_audio.delta` event
2. Forward the base64 string directly to Twilio as a `media` event
3. Convert µ-law to PCM16 for recording only

**Why this approach?** Using matching formats eliminates all real-time resampling and conversion overhead. The `audioop` conversions only happen on the recording path, which is not latency-sensitive. This also eliminates a class of audio quality bugs caused by sample rate mismatches and conversion artifacts.

**Why keep the recording buffer at PCM16?** pydub requires PCM data for MP3 export. Converting µ-law to PCM16 at recording time is cheap and keeps the export path simple.

---

## 8. Error Handling & Edge Cases

| Scenario | How It's Handled |
|----------|-----------------|
| **Call doesn't connect** (busy/no-answer) | Twilio sends `busy` or `no-answer` status → `/status` webhook fires → `done` is set → script exits cleanly |
| **Agent (OpenAI) hangs up first** | Twilio stops streaming → WebSocket receives `stop` event or disconnects → pending task cancelled → cleanup runs |
| **Target agent hangs up first** | Same as above — WebSocket disconnects, pending task cancelled, cleanup runs |
| **Call exceeds 5 minutes** | `_call_timeout_handle` fires → signals completion → `main.py` forces hangup via Twilio API → saves whatever audio was captured |
| **OpenAI WebSocket drops mid-call** | `openai_to_twilio` coroutine catches exception, logs warning. `reconnect()` method available (up to 3 retries) |
| **Audio conversion fails on a chunk** | Exception is caught per-chunk, logged, and skipped. The relay continues with the next chunk |
| **Twilio API call fails** | 3 retries with 30s delay. If all fail, script exits with error |
| **ngrok tunnel fails** | Exit with error code 1 (no retry — network issue needs manual intervention) |
| **Call drops mid-conversation** | Whatever audio was captured up to that point is saved. The transcript is also saved with what it had. No data is lost |
| **Bot speaks before agent picks up** | Deferred greeting: `response.create` is only sent after the first agent audio chunk arrives |
| **Bot sends stale responses after call ends** | `asyncio.wait(FIRST_COMPLETED)` cancels the OpenAI task immediately when Twilio stops |

---

## 9. Persona Matrix

| Key | Name | What It Tests |
|-----|------|--------------|
| `straightforward_scheduler` | Straightforward Scheduler | Baseline conversation quality — polite patient booking a routine physical |
| `rescheduler` | The Rescheduler | State machine handling — books, then immediately reschedules |
| `weekend_warrior` | Weekend Warrior | Calendar logic bugs — insists on Sunday when clinic is closed |
| `urgent_refill` | Urgent Medication Refill | Medical safety — pushes for same-day prescription |
| `insurance_enquirer` | Insurance/Out-of-Pocket Enquirer | Complex knowledge — asks about out-of-network and sliding scale |
| `chronic_interrupter` | Chronic Interrupter | Barge-in handling — constantly cuts off the agent |
| `convoluted_rambler` | Convoluted Rambler | Noise filtering — 45-second rambling story before simple question |
| `distracted_mumbler` | Distracted/Mumbling Patient | VAD thresholds — frequent pauses, "um", "uh", trailing off |
| `multi_tasker` | Multi-Tasker | Token/memory limits — three requests in one sentence |
| `anonymous_canceller` | Anonymous Canceller | Security compliance — demands cancellation but refuses ID |

---

## 10. File-by-File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `src/main.py` | ~200 | CLI orchestrator — ngrok, uvicorn, call lifecycle |
| `src/server.py` | ~270 | FastAPI app — /twiml, /stream WebSocket, /status webhook |
| `src/openai_realtime.py` | ~135 | OpenAI Realtime WebSocket client + GA session config |
| `src/audio_recorder.py` | ~110 | Audio conversion (µ-law ↔ PCM16), silence-padded recording, stereo MP3/TXT export |
| `src/twilio_client.py` | ~73 | Twilio outbound call with retry + hangup |
| `src/personas/prompts.json` | ~62 | 10 persona definitions with system prompts |
| `.env.example` | 7 | Environment variable template |
| `requirements.txt` | 8 | Python package dependencies |
| `README.md` | — | Quick-start setup and usage |
