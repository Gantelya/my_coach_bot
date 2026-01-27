import os
import asyncio
import io
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
import google.generativeai as genai
from fpdf import FPDF
SYSTEM_PROMPT = """
Ты — "Iron Corner", профессиональный тренер по боксу с 20-летним стажем.
Твоя цель: привести пользователя к пиковой форме.
1. Тренировки: составляй планы (мешок, лапы, бой с тенью, ОФП).
2. Питание: считай КБЖУ, анализируй фото еды.
3. Стиль: мотивирующий, жесткий, но справедливый. Используй сленг (джеб, тайминг).
4. Если присылают фото еды: оцени калорийность и скажи, подходит ли это бойцу.
В конце ответа желай "убойного настроя".
"""
TELEGRAM_TOKEN = "8523758786:AAEhTGNnBlhv0nFIll2eAJ6oIhr7_zT3IUo"
GEMINI_KEY = "AIzaSyBQ81mPBqy0R-X_IQ7O9A_46LZJXFUlGyQ"

genai.configure(api_key=GEMINI_KEY, transport='rest')
model = genai.GenerativeModel(
    model_name='models/gemini-pro',
    system_instruction=SYSTEM_PROMPT)

# Вставь сюда свой ID (получи его у @userinfobot), чтобы управлять админкой
ADMIN_ID = 5492881784 

# --- ПАМЯТЬ И СТАТИСТИКА ---
user_history = {} # История диалогов: {user_id: [history]}
all_users = set() # Список всех пользователей для рассылки

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# --- ФУНКЦИИ ---

def create_pdf(user_id, text):
    """Генерация PDF с планом"""
    pdf = FPDF()
    pdf.add_page()
    
    # Пытаемся подключить русский шрифт (должен лежать в папке с ботом как font.ttf)
    try:
        pdf.add_font('CustomFont', '', 'font.ttf')
        pdf.set_font("CustomFont", size=12)
    except:
        # Если шрифта нет, используем стандартный (русский может не отобразиться)
        pdf.set_font("Arial", size=12)
        text = "ERROR: Пожалуйста, загрузи файл font.ttf в репозиторий для поддержки русского языка!"

    # Пишем текст
    pdf.multi_cell(0, 10, txt=text)
    
    filename = f"plan_{user_id}.pdf"
    pdf.output(filename)
    return filename

# --- ХЭНДЛЕРЫ (ОБРАБОТЧИКИ) ---

@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    all_users.add(user_id)
    user_history[user_id] = [] # Очищаем историю при рестарте
    
    await message.answer("В углу ринга! 🥊 Я готов. Расскажи о себе: вес, возраст, цели?")

@dp.message(Command("getplan"))
async def send_plan(message: types.Message):
    user_id = message.from_user.id
    await message.answer("Готовлю твой боевой план... ⏳")

    try:
        # Просим ИИ сделать выжимку для PDF
        chat_session = model.start_chat(history=user_history.get(user_id, []))
        response = chat_session.send_message("Сформируй итоговый четкий план тренировок и питания на неделю.")
        
        # Создаем и отправляем файл
        pdf_path = create_pdf(user_id, response.text)
        document = FSInputFile(pdf_path)
        await message.bot.send_document(message.chat.id, document, caption="Твой план победы! 🏆")
        
        # Убираем мусор
        os.remove(pdf_path)
    except Exception as e:
        await message.answer(f"Сбой в матрице: {e}")

@dp.message(Command("stats"))
async def admin_stats(message: types.Message):
    """Админ-команда: статистика"""
    if message.from_user.id == ADMIN_ID:
        await message.answer(f"📊 Всего бойцов в базе: {len(all_users)}")

@dp.message(Command("broadcast"))
async def admin_broadcast(message: types.Message):
    """Админ-команда: рассылка"""
    if message.from_user.id == ADMIN_ID:
        text = message.text.replace("/broadcast", "").strip()
        if not text:
            await message.answer("Где текст? Пиши: /broadcast Текст")
            return
        
        count = 0
        for uid in all_users:
            try:
                await bot.send_message(uid, f"📢 **ТРЕНЕР НА СВЯЗИ:**\n{text}")
                count += 1
            except:
                pass
        await message.answer(f"Ушло: {count} бойцам.")

    @dp.message(F.photo)
    async def handle_photo(message: types.Message):
        """Обработка фото (зрение)"""
    user_id = message.from_user.id
    all_users.add(user_id)
    
    await message.answer("Анализирую фото... 🧐")
    
    # Скачиваем фото
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    photo_bytes = await bot.download_file(file_info.file_path)
    
    # Готовим данные для Gemini
    img_data = [{"mime_type": "image/jpeg", "data": photo_bytes.getvalue()}]
    
    # Отправляем в чат с историей
    if user_id not in user_history: user_history[user_id] = []
    
    chat_session = model.start_chat(history=user_history[user_id])
    try:
        response = chat_session.send_message(
            content=["Проанализируй это фото (еда или техника) как тренер по боксу:", img_data[0]]
        )
        user_history[user_id] = chat_session.history
        await message.reply(response.text)
    except Exception as e:
        await message.reply("Не вижу картинку. Попробуй еще раз.")

@dp.message()
async def chat_text(message: types.Message):
    """Обработка обычного текста"""
    user_id = message.from_user.id
    all_users.add(user_id)
    
    if user_id not in user_history:
        user_history[user_id] = []

    chat_session = model.start_chat(history=user_history[user_id])
    
    try:
        response = chat_session.send_message(message.text)
        user_history[user_id] = chat_session.history
        await message.reply(response.text)
    except Exception as e:
        await message.reply(f"Ошибка Gemini: {e}")

# --- ЗАПУСК ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
