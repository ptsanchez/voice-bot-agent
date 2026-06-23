import logging

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

logger = logging.getLogger(__name__)


MAX_RETRIES = 3
RETRY_DELAY_S = 30


class TwilioClientError(Exception):
    pass


class TwilioCallHandler:
    def __init__(self, account_sid: str, auth_token: str, from_number: str):
        self._client = Client(account_sid, auth_token)
        self._from_number = from_number

    def initiate_call(
        self,
        to_number: str,
        twiml_url: str,
        status_callback_url: str,
    ) -> str:
        last_error: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                call = self._client.calls.create(
                    url=twiml_url,
                    to=to_number,
                    from_=self._from_number,
                    status_callback=status_callback_url,
                    status_callback_event=[
                        "completed",
                        "failed",
                        "busy",
                        "no-answer",
                    ],
                    timeout=300,
                )
                logger.info(
                    "Call initiated (attempt %d/%d): SID=%s",
                    attempt,
                    MAX_RETRIES,
                    call.sid,
                )
                return call.sid
            except TwilioRestException as e:
                last_error = e
                logger.warning(
                    "Call initiation failed (attempt %d/%d): %s",
                    attempt,
                    MAX_RETRIES,
                    e,
                )
                if attempt < MAX_RETRIES:
                    import time
                    time.sleep(RETRY_DELAY_S)

        raise TwilioClientError(
            f"Failed to initiate call after {MAX_RETRIES} attempts"
        ) from last_error

    def hangup_call(self, call_sid: str):
        try:
            self._client.calls(call_sid).update(status="completed")
            logger.info("Hangup requested for call %s", call_sid)
        except TwilioRestException as e:
            logger.warning("Failed to hangup call %s: %s", call_sid, e)