"""OpenAI speech-to-text for uploaded MP3s, split with ffmpeg so each request fits the API's file limit."""

import asyncio
from pathlib import Path
import tempfile

from openai import AsyncOpenAI, omit


class TranscriptionClient:
    MODEL = "gpt-transcribe"  # Not whisper-1: it is deprecated and shuts down 2027-02-26.
    # The API rejects files over 25 MB. 20 minutes of mono 64 kbps audio is about 10 MB,
    # and 64 kbps is plenty for speech.
    SEGMENT_SECONDS = 20 * 60
    BITRATE = "64k"
    PROMPT_WORDS = 200  # Tail of the previous segment's text, so wording and spellings carry across cuts.
    ERROR_LINES = 3  # ffmpeg's last stderr lines name the problem.

    def __init__(self, openai: AsyncOpenAI):
        self.openai = openai

    async def transcribe(self, audio: bytes) -> str:
        """The recording's text, transcribed segment by segment in order. Any failing segment fails the call."""
        with tempfile.TemporaryDirectory() as directory:
            segments = await self.split(audio, Path(directory))
            texts = []
            prompt = ""  # The first segment has no earlier text.
            for segment in segments:
                text = await self.transcribe_segment(segment, prompt)
                texts.append(text)
                prompt = " ".join(text.split()[-self.PROMPT_WORDS:])
        return "\n".join(texts)

    async def split(self, audio: bytes, directory: Path) -> list[Path]:
        """Re-encode to mono at BITRATE and cut into SEGMENT_SECONDS pieces; returns them in order."""
        source = directory / "in.mp3"
        source.write_bytes(audio)
        ffmpeg = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-i", str(source),
            "-vn",  # Drop embedded cover art, which ffmpeg would otherwise re-encode into a segment.
            "-ac", "1", "-b:a", self.BITRATE, "-f", "segment", "-segment_time", str(self.SEGMENT_SECONDS),
            str(directory / "seg_%03d.mp3"),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, stderr = await ffmpeg.communicate()
        if ffmpeg.returncode != 0:
            tail = "\n".join(stderr.decode(errors="replace").strip().splitlines()[-self.ERROR_LINES:])
            raise RuntimeError(f"ffmpeg could not read the audio: {tail}")
        return sorted(directory.glob("seg_*.mp3"))

    async def transcribe_segment(self, segment: Path, prompt: str) -> str:
        """One API call. `prompt` is the end of the previous segment's text; the first segment sends none."""
        response = await self.openai.audio.transcriptions.create(model=self.MODEL, file=segment,
                                                                 prompt=prompt or omit)
        return response.text
