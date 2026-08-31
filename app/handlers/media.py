import io
import logging
from aiogram import Router, Bot, F
from aiogram.enums import ChatAction
from aiogram.types import Message

from aiogram.fsm.context import FSMContext

from app.services.llm import llm_service
from app.handlers.text import execute_action_pipeline, safe_answer_markdown

logger = logging.getLogger(__name__)
router = Router(name="media")


@router.message(F.photo | F.document)
async def handle_media_message(message: Message, bot: Bot, state: FSMContext):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)
    
    caption = message.caption or ""
    image_bytes = None
    mime_type = "image/jpeg"

    try:
        if message.photo:
            # Download highest resolution photo
            photo = message.photo[-1]
            file_info = await bot.get_file(photo.file_id)
            file_stream = io.BytesIO()
            await bot.download_file(file_info.file_path, file_stream)
            image_bytes = file_stream.getvalue()
        elif message.document:
            mime = message.document.mime_type or ""
            if mime.startswith("image/"):
                mime_type = mime
                file_info = await bot.get_file(message.document.file_id)
                file_stream = io.BytesIO()
                await bot.download_file(file_info.file_path, file_stream)
                image_bytes = file_stream.getvalue()
            else:
                await message.answer("⚠️ Пожалуйста, отправьте изображение (справку, чек, расписание или билет).")
                return

        if not image_bytes:
            await message.answer("❌ Не удалось загрузить изображение.")
            return

        logger.info(f"Processing media image message with vision LLM. Caption: '{caption}'")
        parsed_action = await llm_service.parse_user_request(
            text_content=caption,
            image_bytes=image_bytes,
            mime_type=mime_type
        )
        
        reply_text, reply_markup = await execute_action_pipeline(bot, message.chat.id, parsed_action, state=state, user_text=caption)
        await safe_answer_markdown(message, reply_text, reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error handling media vision message: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка распознавания изображения: {str(e)}", parse_mode=None)
