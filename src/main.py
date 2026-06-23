import argparse
import asyncio
import json
import logging
import os
import sys
import time

from datetime import datetime, timezone

from dotenv import load_dotenv
from pyngrok import ngrok, conf as ngrok_conf
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server import app, state, CALL_TIMEOUT_S
from src.audio_recorder import AudioRecorder
from src.twilio_client import TwilioCallHandler, TwilioClientError

logger = logging.getLogger(__name__)

OUTPUT_DIR = "conversations"
NGROK_PORT = 8765


def _load_persona(key: str) -> dict:
    prompts_path = os.path.join(os.path.dirname(__file__), "personas", "prompts.json")
    with open(prompts_path) as f:
        personas = json.load(f)

    for p in personas:
        if p["key"] == key:
            return p

    available = [p["key"] for p in personas]
    print(f"Unknown persona: {key}")
    print(f"Available: {', '.join(available)}")
    sys.exit(1)


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("pyngrok").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def _start_ngrok() -> str:
    auth_token = os.getenv("NGROK_AUTHTOKEN")
    if not auth_token:
        print("ERROR: NGROK_AUTHTOKEN not set in .env")
        sys.exit(1)

    ngrok_conf.get_default().auth_token = auth_token
    tunnel = ngrok.connect(NGROK_PORT, "http")
    public_url = tunnel.public_url.replace("http://", "https://")
    logger.info("ngrok tunnel opened: %s", public_url)
    return public_url


def _artifact_paths(persona_key: str) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H-%M-%S")
    base = os.path.join(OUTPUT_DIR, f"{ts}_{persona_key}")
    return f"{base}.mp3", f"{base}.txt"


async def _run_persona(persona: dict):
    persona_key = persona["key"]
    persona_prompt = (
        "IMPORTANT: You MUST speak only in English. "
        "If the other person speaks in another language, respond in English. "
        "Never switch to another language.\n\n"
        + persona["system_prompt"]
    )
    print(f"\n{'='*60}")
    print(f"Persona: {persona['name']} ({persona_key})")
    print(f"Description: {persona['description']}")
    print(f"{'='*60}\n")

    _ensure_output_dir()
    mp3_path, txt_path = _artifact_paths(persona_key)

    recorder = AudioRecorder()

    state.persona_prompt = persona_prompt
    state.recorder = recorder
    state.ngrok_url = ""
    state.call_sid = ""
    state.call_connected = False
    state.call_completed = False
    state.done = asyncio.Event()
    state.openai_api_key = os.getenv("OPENAI_API_KEY", "")
    state.openai_model = os.getenv("OPENAI_MODEL", "gpt-realtime-1.5")

    if not state.openai_api_key:
        print("ERROR: OPENAI_API_KEY not set in .env")
        sys.exit(1)

    twilio_handler = TwilioCallHandler(
        account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
        from_number=os.getenv("TWILIO_PHONE_NUMBER", ""),
    )

    agent_number = os.getenv("AGENT_PHONE_NUMBER", "")
    if not agent_number:
        print("ERROR: AGENT_PHONE_NUMBER not set in .env")
        sys.exit(1)

    print("Starting ngrok tunnel...")
    state.ngrok_url = _start_ngrok()

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=NGROK_PORT,
        log_level="info",
        lifespan="on",
    )
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server.serve())

    await asyncio.sleep(1.0)

    twiml_url = f"{state.ngrok_url}/twiml"
    callback_url = f"{state.ngrok_url}/status"

    print(f"Twiml URL: {twiml_url}")
    print(f"Status callback URL: {callback_url}")
    print(f"Initiating call to {agent_number}...")

    try:
        call_sid = twilio_handler.initiate_call(
            to_number=agent_number,
            twiml_url=twiml_url,
            status_callback_url=callback_url,
        )
        state.call_sid = call_sid
        print(f"Call initiated: SID={call_sid}")
    except TwilioClientError as e:
        print(f"ERROR: Failed to initiate call: {e}")
        server_task.cancel()
        ngrok.disconnect(state.ngrok_url)
        return

    print(f"Waiting for call to complete (timeout: {CALL_TIMEOUT_S}s)...")
    try:
        await asyncio.wait_for(state.done.wait(), timeout=CALL_TIMEOUT_S + 10)
    except asyncio.TimeoutError:
        print("ERROR: Call did not complete within timeout")
        twilio_handler.hangup_call(call_sid)

    call_duration = time.monotonic() - recorder.start_time
    print(f"\nCall duration: {call_duration:.1f}s")

    loop = asyncio.get_running_loop()
    print("Saving artifacts...")
    await loop.run_in_executor(None, recorder.save_mp3, mp3_path)
    await loop.run_in_executor(None, recorder.save_txt, txt_path)
    print(f"  Audio: {mp3_path}")
    print(f"  Transcript: {txt_path}")

    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass

    ngrok.disconnect(state.ngrok_url)
    print("Done.\n")


async def main():
    _setup_logging()

    parser = argparse.ArgumentParser(description="Pretty Good AI Voice Bot Agent")
    parser.add_argument(
        "--persona",
        required=True,
        help="Persona key from prompts.json (e.g., straightforward_scheduler)",
    )
    args = parser.parse_args()

    load_dotenv()
    persona = _load_persona(args.persona)

    await _run_persona(persona)


if __name__ == "__main__":
    asyncio.run(main())