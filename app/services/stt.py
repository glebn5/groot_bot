import os
import logging
from groq import AsyncGroq
from app.config import settings

logger = logging.getLogger(__name__)


class STTService:
    def __init__(self):
        self.client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.model = "whisper-large-v3-turbo"

    async def transcribe_audio_file(self, file_path: str) -> str:
        """
        Transcribe audio file (.ogg/.mp3/.wav) using Groq Whisper API.
        """
        try:
            logger.info(f"Transcribing audio file with Groq Whisper: {file_path}")
            with open(file_path, "rb") as audio_file:
                transcription = await self.client.audio.transcriptions.create(
                    file=(os.path.basename(file_path), audio_file.read()),
                    model=self.model,
                    response_format="text"
                )
            text = str(transcription).strip()
            logger.info(f"Transcription result: {text}")
            return text
        except Exception as e:
            logger.error(f"Error during audio transcription: {e}", exc_info=True)
            raise RuntimeError(f"Failed to transcribe voice message: {str(e)}")


stt_service = STTService()
