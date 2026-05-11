from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class VoiceProcessor:
    def __init__(self, whisper_model_name: str = "base"):
        self.whisper_model_name = whisper_model_name
        self._whisper_model: WhisperModel | None = None

    def _get_whisper(self) -> WhisperModel:
        if self._whisper_model is None:
            try:
                from faster_whisper import WhisperModel

                logger.info(f"Loading Whisper model: {self.whisper_model_name}")
                self._whisper_model = WhisperModel(
                    self.whisper_model_name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=4,
                )
            except ImportError:
                raise RuntimeError(
                    "faster-whisper is not installed. Run: pip install faster-whisper"
                )
        return self._whisper_model

    def transcribe(self, audio_path: str | Path) -> str:
        model = self._get_whisper()
        segments, info = model.transcribe(str(audio_path), beam_size=5)
        text = "".join([segment.text for segment in segments]).strip()
        return text

    async def synthesize(
        self,
        text: str,
        output_path: str | Path,
        voice: str = "ru-RU-SvetlanaNeural",
    ) -> None:
        try:
            import edge_tts

            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(output_path))
        except ImportError:
            raise RuntimeError("edge-tts is not installed. Run: pip install edge-tts")
        except Exception as e:
            logger.error(f"TTS Error: {e}")
            raise
