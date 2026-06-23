import array
import audioop
import threading
import time

from pydub import AudioSegment


AUDIO_SAMPLE_RATE = 8000
AUDIO_SAMPLE_WIDTH = 2


def ulaw_to_pcm16(mu_law_data: bytes) -> bytes:
    return audioop.ulaw2lin(mu_law_data, AUDIO_SAMPLE_WIDTH)


def pcm16_to_ulaw(pcm16_data: bytes) -> bytes:
    return audioop.lin2ulaw(pcm16_data, AUDIO_SAMPLE_WIDTH)


def resample_pcm16(data: bytes, from_rate: int, to_rate: int) -> bytes:
    if from_rate == to_rate:
        return data
    result, _ = audioop.ratecv(data, AUDIO_SAMPLE_WIDTH, 1, from_rate, to_rate, None)
    return result


class AudioRecorder:
    def __init__(self):
        self._agent_buffer = bytearray()
        self._bot_buffer = bytearray()
        self._transcripts: list[dict] = []
        self._lock = threading.Lock()
        self._start_time = time.monotonic()

    @property
    def start_time(self) -> float:
        return self._start_time

    def _relative_timestamp(self) -> str:
        elapsed = time.monotonic() - self._start_time
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        return f"{minutes:02d}:{seconds:02d}"

    def add_agent_audio(self, mu_law_data: bytes):
        pcm16 = ulaw_to_pcm16(mu_law_data)
        with self._lock:
            self._agent_buffer.extend(pcm16)

    def add_bot_audio(self, pcm16_data: bytes):
        with self._lock:
            self._bot_buffer.extend(pcm16_data)

    def add_transcript(self, speaker: str, text: str):
        ts = self._relative_timestamp()
        with self._lock:
            self._transcripts.append({"timestamp": ts, "speaker": speaker, "text": text})

    def save_mp3(self, path: str):
        with self._lock:
            agent = bytes(self._agent_buffer)
            bot = bytes(self._bot_buffer)

        seg_agent = AudioSegment(
            data=agent,
            sample_width=AUDIO_SAMPLE_WIDTH,
            frame_rate=AUDIO_SAMPLE_RATE,
            channels=1,
        )
        seg_bot = AudioSegment(
            data=bot,
            sample_width=AUDIO_SAMPLE_WIDTH,
            frame_rate=AUDIO_SAMPLE_RATE,
            channels=1,
        )

        # Pad both to the same number of samples to avoid off-by-one errors
        # in from_mono_audiosegments (duration-based padding can round differently)
        samples_agent = seg_agent.get_array_of_samples()
        samples_bot = seg_bot.get_array_of_samples()
        max_samples = max(len(samples_agent), len(samples_bot))
        samples_agent.extend([0] * (max_samples - len(samples_agent)))
        samples_bot.extend([0] * (max_samples - len(samples_bot)))

        # Interleave samples into stereo
        stereo_data = array.array(samples_agent.typecode)
        for a, b in zip(samples_agent, samples_bot):
            stereo_data.append(a)
            stereo_data.append(b)

        stereo = AudioSegment(
            data=stereo_data.tobytes(),
            sample_width=AUDIO_SAMPLE_WIDTH,
            frame_rate=AUDIO_SAMPLE_RATE,
            channels=2,
        )
        stereo.export(path, format="mp3", bitrate="64k")

    def save_txt(self, path: str):
        with self._lock:
            lines = list(self._transcripts)

        with open(path, "w") as f:
            for entry in lines:
                f.write(f"[{entry['timestamp']}] {entry['speaker']}: {entry['text']}\n")