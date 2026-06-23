# Pretty Good AI — Voice Bot Agent

Automated voice-based testing bot that simulates realistic patient conversations with the Pretty Good AI medical voice agent using the OpenAI Realtime API over WebSocket.

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
#    OPENAI_API_KEY          — OpenAI API key (Realtime API access required)
#    OPENAI_MODEL            — (optional) defaults to gpt-4o-realtime-preview-2024-12-17
#    TWILIO_ACCOUNT_SID      — Twilio account SID
#    TWILIO_AUTH_TOKEN       — Twilio auth token
#    TWILIO_PHONE_NUMBER     — Your purchased Twilio number (e.g., +15625696327)
#    AGENT_PHONE_NUMBER      — Target Pretty Good AI agent number (+18054398008)
#    NGROK_AUTHTOKEN         — ngrok authtoken
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

- `{YYYY-MM-DD}T{HH-MM-SS}_{persona_key}.mp3` — mixed two-way audio (agent left, bot right channel)
- `{YYYY-MM-DD}T{HH-MM-SS}_{persona_key}.txt` — timed transcript with `[Agent]` / `[Bot]` labels

## Architecture

```
[Pretty Good AI Agent] ← PSTN ← [Twilio] ←µ-law WS→ [FastAPI/ngrok] ←PCM16 WS→ [OpenAI Realtime API]
                                                                  ↕
                                                            [Audio Recorder] → .mp3 + .txt
```

1. `main.py` loads the persona, starts an ngrok tunnel, and launches the FastAPI server
2. Twilio initiates an outbound call to the Pretty Good AI agent number
3. Twilio's `<Connect><Stream>` TwiML directive streams bidirectional 8kHz µ-law audio via WebSocket to the server
4. The server relays audio between Twilio (8kHz µ-law) and OpenAI (24kHz PCM16) with format conversion
5. `AudioRecorder` captures both streams and transcript events, saving artifacts on call completion

## Error Handling

- 3 retries on call initiation (30s apart)
- 3 reconnect attempts for OpenAI Realtime API disconnects
- 5-minute per-call timeout with forced hangup
- Partial audio saved if call drops mid-conversation
