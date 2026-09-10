import os
import tempfile
import logging
from aiogram import Router, Bot, F
from aiogram.enums import ChatAction
from aiogram.types import Message

from aiogram.fsm.context import FSMContext

from app.services.stt import stt_service
from app.agent import groot_agent
from app.handlers.text import safe_answer_markdown

logger = logging.getLogger(__name__)
router = Router(name="voice")


@router.message(F.voice | F.audio)
async def handle_voice_message(message: Message, bot: Bot, state: FSMContext):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.RECORD_VOICE)
    
    voice_or_audio = message.voice or message.audio
    if not voice_or_audio:
        await message.answer("❌ Не удалось получить аудиофайл.")
        return

    tmp_path = None
    try:
        # Download voice file from Telegram
        file_info = await bot.get_file(voice_or_audio.file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp_file:
            tmp_path = tmp_file.name

        await bot.download_file(file_info.file_path, tmp_path)
        logger.info(f"Downloaded voice message to temporary file: {tmp_path}")

        # Transcribe audio using Groq Whisper API
        transcribed_text = await stt_service.transcribe_audio_file(tmp_path)
        user_info = f"user_id={message.from_user.id}"
        if message.from_user.username:
            user_info += f" (@{message.from_user.username})"
        logger.info(f"Received voice message from {user_info}, transcribed: '{transcribed_text}'")
        
        # Inform user of transcribed text
        await safe_answer_markdown(message, f"🎙 **Расшифровка голоса:**\n\n{transcribed_text}")

        # Process request with Groot Agent
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        agent_res = await groot_agent.process_message(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            user_text=transcribed_text
        )
        await safe_answer_markdown(message, agent_res.reply_text)

    except Exception as e:
        logger.error(f"Error handling voice message: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка обработки голосового сообщения: {str(e)}", parse_mode=None)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
