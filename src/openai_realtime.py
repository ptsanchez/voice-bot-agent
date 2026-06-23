import asyncio
import json
import logging

import websockets

logger = logging.getLogger(__name__)


RECONNECT_MAX_ATTEMPTS = 3
RECONNECT_DELAY_S = 2.0


class OpenAIClientError(Exception):
    pass


class OpenAIRealtimeClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        instructions: str,
        voice: str = "alloy",
    ):
        self._api_key = api_key
        self._model = model
        self._instructions = instructions
        self._voice = voice
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._reconnect_attempts = 0
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()

    async def connect(self):
        self._reconnect_attempts = 0
        await self._do_connect()

    async def _do_connect(self):
        url = f"wss://api.openai.com/v1/realtime?model={self._model}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }

        try:
            self._ws = await websockets.connect(url, additional_headers=headers)
        except Exception as e:
            logger.error("Failed to connect to OpenAI Realtime API: %s", e)
            raise OpenAIClientError(f"Connection failed: {e}")

        session_event = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "output_modalities": ["audio"],
                "instructions": self._instructions,
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcmu",
                        },
                        "turn_detection": {
                            "type": "server_vad",
                        },
                        "transcription": {
                            "model": "whisper-1",
                        },
                    },
                    "output": {
                        "format": {
                            "type": "audio/pcmu",
                        },
                        "voice": self._voice,
                    },
                },
            },
        }
        await self._send(session_event)

        self._reconnect_attempts = 0

    async def reconnect(self) -> bool:
        if self._reconnect_attempts >= RECONNECT_MAX_ATTEMPTS:
            logger.error("Reconnect attempts exhausted (%d)", RECONNECT_MAX_ATTEMPTS)
            return False

        self._reconnect_attempts += 1
        logger.info(
            "Reconnecting to OpenAI (attempt %d/%d)...",
            self._reconnect_attempts,
            RECONNECT_MAX_ATTEMPTS,
        )
        await asyncio.sleep(RECONNECT_DELAY_S * self._reconnect_attempts)
        await self._do_connect()
        return True

    async def _send(self, data: dict):
        if self._ws is None:
            raise OpenAIClientError("Not connected")
        await self._ws.send(json.dumps(data))

    async def send_audio(self, pcm16_base64: str):
        await self._send({
            "type": "input_audio_buffer.append",
            "audio": pcm16_base64,
        })

    async def send_response_create(self, instructions_override: str | None = None):
        payload: dict = {
            "type": "response.create",
            "response": {},
        }
        if instructions_override:
            payload["response"]["instructions"] = instructions_override
        await self._send(payload)

    async def cancel_response(self):
        await self._send({
            "type": "response.cancel",
        })

    async def receive_events(self):
        if self._ws is None:
            raise OpenAIClientError("Not connected")
        async for raw in self._ws:
            event = json.loads(raw)
            yield event

    async def close(self):
        if self._ws:
            await self._ws.close()
            self._ws = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None