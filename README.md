# Health Voice Bot Agent

An automated voice-based testing tool that simulates realistic patient phone calls to any target medical voice agent using Twilio and the OpenAI Realtime API.

## What It Does

1. Dials a target phone number using Twilio
2. Streams live audio between Twilio and the **OpenAI Realtime API** (speech-to-speech model)
3. The AI model plays the role of a patient based on a configurable **persona script**
4. Records both sides of the conversation as a stereo MP3 and a timestamped text transcript

## Prerequisites

- **Python 3.10+**
- **FFmpeg** (system dependency for `pydub` audio export):
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`
- **Twilio account** with a purchased phone number (E.164 format)
- **OpenAI API key** with access to the Realtime API
- **ngrok account** (free tier works) with auth token

## Setup

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env

# 3. Edit .env and fill in your credentials:
#    OPENAI_API_KEY      — OpenAI API key (Realtime API access required)
#    OPENAI_MODEL        — (optional) defaults to gpt-realtime-1.5
#    TWILIO_ACCOUNT_SID  — Twilio account SID
#    TWILIO_AUTH_TOKEN   — Twilio auth token
#    TWILIO_PHONE_NUMBER — Your purchased Twilio number (e.g., +12223334444)
#    AGENT_PHONE_NUMBER  — Target voice agent number to call
#    NGROK_AUTHTOKEN     — ngrok authtoken
```

## Usage

Run a single persona:

```bash
python src/main.py --persona straightforward_scheduler
```

Available personas:

| Key | Description |
|-----|-------------|
| `straightforward_scheduler` | Base case — books a routine physical |
| `rescheduler` | Books then immediately reschedules |
| `weekend_warrior` | Insists on Sunday booking |
| `urgent_refill` | Needs immediate medication refill |
| `insurance_enquirer` | Asks about out-of-network coverage |
| `chronic_interrupter` | Constantly interrupts the agent |
| `convoluted_rambler` | Long rambling narrative before simple question |
| `distracted_mumbler` | Speaks with pauses and filler words |
| `multi_tasker` | Requests three actions in one sentence |
| `anonymous_canceller` | Refuses to identify for cancellation |

## Output

Artifacts are written to the `conversations/` directory:

- `{YYYY-MM-DD}T{HH-MM-SS}_{persona_key}.mp3` — stereo audio (left = agent/patient, right = target voice agent)
- `{YYYY-MM-DD}T{HH-MM-SS}_{persona_key}.txt` — timed transcript with `[Agent]` / `[Bot]` labels

## Architecture

```
[Target Voice Agent] ← PSTN ← [Twilio] ←µ-law WS→ [FastAPI/ngrok] ←µ-law WS→ [OpenAI Realtime API]
                                                             ↕
                                                       [Audio Recorder] → .mp3 + .txt
```

1. `main.py` loads the persona, starts an ngrok tunnel, and launches the FastAPI server
2. Twilio initiates an outbound call to the target voice agent number
3. Twilio's `<Connect><Stream>` TwiML directive streams bidirectional 8kHz µ-law audio via WebSocket to the server
4. The server relays audio between Twilio and OpenAI — both use 8kHz µ-law natively, so no format conversion is needed
5. The bot waits for the agent to speak first before responding (deferred greeting)
6. `AudioRecorder` captures both streams with real-time silence padding for natural playback, saving artifacts on call completion

## Error Handling

- 3 retries on call initiation (30s apart)
- 3 reconnect attempts for OpenAI Realtime API disconnects
- 5-minute per-call timeout with forced hangup
- Partial audio saved if call drops mid-conversation
- Immediate task cancellation when either side hangs up (no stale responses)
