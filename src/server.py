import asyncio
import base64
import json
import logging

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect

from src.audio_recorder import (
    AudioRecorder,
    ulaw_to_pcm16,
)
from src.openai_realtime import OpenAIRealtimeClient, OpenAIClientError

logger = logging.getLogger(__name__)

TWILIO_SAMPLE_RATE = 8000
OPENAI_SAMPLE_RATE = 24000

app = FastAPI()
router = app

logger.info("FastAPI app initialized")


CALL_TIMEOUT_S = 300


_call_timeout_handle: asyncio.TimerHandle | None = None


class CallState:
    def __init__(self):
        self.persona_prompt: str = ""
        self.ngrok_url: str = ""
        self.recorder: AudioRecorder | None = None
        self.done: asyncio.Event = asyncio.Event()
        self.call_sid: str = ""
        self.call_connected: bool = False
        self.call_completed: bool = False
        self.openai_api_key: str = ""
        self.openai_model: str = ""


state = CallState()


def _build_twiml(ws_url: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}"/>
  </Connect>
</Response>"""


@router.post("/twiml")
async def twiml_endpoint():
    ws_url = state.ngrok_url.replace("https://", "wss://") + "/stream"
    return Response(content=_build_twiml(ws_url), media_type="application/xml")


@router.post("/status")
async def status_webhook(request: Request):
    form = await request.form()
    data = dict(form)
    call_status = data.get("CallStatus", "")
    sid = data.get("CallSid", "")

    logger.info("Status webhook: SID=%s status=%s", sid, call_status)

    if call_status in ("completed", "failed", "busy", "no-answer"):
        state.call_completed = True
        state.done.set()

    return {"ok": True}


@router.websocket("/stream")
async def stream_websocket(ws: WebSocket):
    await ws.accept()
    logger.info("WebSocket /stream connection accepted")

    openai_client = OpenAIRealtimeClient(
        api_key=state.openai_api_key,
        model=state.openai_model,
        instructions=state.persona_prompt,
    )

    stream_sid: str | None = None

    try:
        # Twilio sends a 'connected' event first, then a 'start' event
        connected_msg = await asyncio.wait_for(ws.receive_json(), timeout=30)
        if connected_msg.get("event") == "connected":
            logger.info("Twilio WebSocket connected event received")
        else:
            # If it's not 'connected', it might be the 'start' event directly
            # (re-queue the message conceptually by treating it as start_msg below)
            pass  # handled below

        start_msg = await asyncio.wait_for(ws.receive_json(), timeout=30)
        if start_msg.get("event") != "start":
            logger.error("Expected 'start' event, got: %s", start_msg.get("event"))
            await ws.close()
            return

        stream_sid = start_msg["start"]["streamSid"]
        logger.info("Stream started: SID=%s", stream_sid)

        state.call_connected = True

        await openai_client.connect()

        _schedule_timeout()

        await openai_client.send_response_create(
            instructions_override=(
                "Start the conversation with a natural greeting. "
                "Do not describe yourself or ask how you can help yet — "
                "just greet the patient briefly and naturally."
            )
        )

        twilio_audio_buffer = bytearray()
        openai_audio_buffer = bytearray()
        response_active = False

        async def twilio_to_openai():
            nonlocal twilio_audio_buffer
            try:
                while True:
                    raw = await ws.receive_json()
                    event_type = raw.get("event")

                    if event_type == "media":
                        payload_b64 = raw["media"]["payload"]
                        mu_law_chunk = base64.b64decode(payload_b64)
                        twilio_audio_buffer.extend(mu_law_chunk)

                        try:
                            # With audio/pcmu, forward µ-law bytes directly to OpenAI
                            await openai_client.send_audio(payload_b64)
                        except Exception as e:
                            logger.warning("Audio error (Twilio→OpenAI): %s", e)
                            continue

                        if state.recorder:
                            state.recorder.add_agent_audio(mu_law_chunk)

                    elif event_type == "stop":
                        logger.info("Twilio stream stopped")
                        break

            except WebSocketDisconnect:
                logger.info("Twilio WebSocket disconnected")
            except Exception as e:
                logger.warning("Error in twilio_to_openai: %s", e)

        async def openai_to_twilio():
            nonlocal openai_audio_buffer, response_active
            try:
                async for event in openai_client.receive_events():
                    etype = event.get("type")

                    if etype == "response.output_audio.delta":
                        delta_b64 = event.get("delta", "")
                        mu_law_chunk = base64.b64decode(delta_b64)
                        openai_audio_buffer.extend(mu_law_chunk)

                        try:
                            # With audio/pcmu, forward µ-law bytes directly to Twilio
                            await ws.send_json({
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": delta_b64},
                            })
                        except Exception as e:
                            logger.warning("Audio error (OpenAI→Twilio): %s", e)
                            continue

                        if state.recorder:
                            # Convert µ-law to PCM16 for recording
                            state.recorder.add_bot_audio(ulaw_to_pcm16(mu_law_chunk))

                    elif etype == "conversation.item.input_audio_transcription.completed":
                        transcript = event.get("transcript", "").strip()
                        if transcript and state.recorder:
                            state.recorder.add_transcript("Agent", transcript)

                    elif etype == "response.output_audio_transcript.done":
                        transcript = event.get("transcript", "").strip()
                        if transcript and state.recorder:
                            state.recorder.add_transcript("Bot", transcript)

                    elif etype == "response.created":
                        response_active = True

                    elif etype == "response.done":
                        response_active = False

                    elif etype == "input_audio_buffer.speech_started":
                        if response_active:
                            try:
                                await openai_client.cancel_response()
                                response_active = False
                            except Exception:
                                pass

                    elif etype == "error":
                        # Suppress benign cancel-not-active errors from barge-in
                        error_code = event.get("error", {}).get("code", "")
                        if error_code == "response_cancel_not_active":
                            continue
                        logger.error("OpenAI Realtime API error: %s", event)

            except Exception as e:
                logger.warning("Error in openai_to_twilio: %s", e)

        await asyncio.gather(twilio_to_openai(), openai_to_twilio())

    except asyncio.TimeoutError:
        logger.error("Timeout waiting for Twilio start event")
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error("Stream error: %s", e)
    finally:
        await openai_client.close()
        _cancel_timeout()
        if not state.call_completed:
            state.call_completed = True
            state.done.set()
        logger.info("Stream %s closed", stream_sid or "?")


def _schedule_timeout():
    global _call_timeout_handle

    async def _timeout():
        logger.warning("Call timeout (%ds) reached", CALL_TIMEOUT_S)
        if not state.call_completed:
            state.call_completed = True
            state.done.set()

    _cancel_timeout()

    loop = asyncio.get_event_loop()
    _call_timeout_handle = loop.call_later(CALL_TIMEOUT_S, lambda: asyncio.ensure_future(_timeout()))


def _cancel_timeout():
    global _call_timeout_handle
    if _call_timeout_handle is not None:
        _call_timeout_handle.cancel()
        _call_timeout_handle = None