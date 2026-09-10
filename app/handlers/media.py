import io
import logging
from aiogram import Router, Bot, F
from aiogram.enums import ChatAction
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from app.agent import groot_agent
from app.agent.providers import llm_provider
from app.handlers.text import safe_answer_markdown

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

        logger.info(f"Processing media image message with vision. Caption: '{caption}'")
        extracted_text = await llm_provider.extract_vision_text(
            image_bytes=image_bytes,
            mime_type=mime_type,
            user_caption=caption
        )

        if extracted_text:
            combined_request = f"[Распознанное содержимое фото/документа]:\n{extracted_text}"
            if caption:
                combined_request += f"\n\nПросьба пользователя: {caption}"
            else:
                combined_request += "\n\nПожалуйста, проанализируй это и создай соответствующие события, напоминания или задачи."

            agent_res = await groot_agent.process_message(
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                user_text=combined_request
            )
            await safe_answer_markdown(message, agent_res.reply_text)
        elif caption and caption.strip():
            # If vision not configured but user supplied caption, process caption
            agent_res = await groot_agent.process_message(
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                user_text=caption
            )
            await safe_answer_markdown(message, agent_res.reply_text)
        else:
            instruction = (
                "🌴 Я получил фото! Чтобы я мог автоматически считывать с него текст (талоны к врачу, чеки, справки), "
                "укажите бесплатный **Gemini API Key** через команду `/settings`. "
                "Либо отправьте фото вместе с текстом-описанием того, что нужно сделать!"
            )
            await message.answer(instruction, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error handling media vision message: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка распознавания изображения: {str(e)}", parse_mode=None)
