import os
import asyncio
import io
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
import google.generativeai as genai
from fpdf import FPDF
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from PIL import Image

# Фейковый сервер для Render
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive")
    
    def log_message(self, format, *args):
        pass

def run_health_check():
    server = HTTPServer(('0.0.0.0', 10000), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_check, daemon=True).start()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_KEY")

SYSTEM_PROMPT = """
Ты — "Iron Corner", профессиональный тренер по боксу с 20-летним стажем.
Твоя цель: привести пользователя к пиковой форме.
1. Тренировки: составляй планы (мешок, лапы, бой с тенью, ОФП).
2. Питание: считай КБЖУ, анализируй фото еды.
3. Стиль: мотивирующий, жесткий, но справедливый. Используй сленг (джеб, тайминг).
4. Если присылают фото еды: оцени калорийность и скажи, подходит ли это бойцу.
В конце ответа желай "убойного настроя".
"""

# ИСПРАВЛЕНИЕ: конфигурация с транспортом для стабильного API
genai.configure(
    api_key=GEMINI_KEY,
    transport='rest'  # Принудительно используем REST API (не gRPC)
)

ADMIN_ID = 5492881784 

user_history = {}
all_users = set()

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# --- ФУНКЦИИ ---

def get_model():
    """Создание модели с правильными параметрами"""
    return genai.GenerativeModel(
        model_name='models/gemini-1.5-flash',  # Полное имя модели
        generation_config={
            'temperature': 0.7,
            'top_p': 0.95,
            'top_k': 40,
            'max_output_tokens': 2048,
        }
    )

def create_pdf(user_id, text):
    """Генерация PDF с планом"""
    pdf = FPDF()
    pdf.add_page()
    
    try:
        pdf.add_font('CustomFont', '', 'font.ttf')
        pdf.set_font("CustomFont", size=12)
    except:
        try:
            pdf.add_font('DejaVu', '', 'DejaVuSans.ttf')
            pdf.set_font("DejaVu", size=12)
        except:
            pdf.set_font("Arial", size=12)
            text = "ERROR: Загрузите шрифт для кириллицы!"

    for line in text.split('\n'):
        pdf.multi_cell(0, 10, txt=line)
    
    filename = f"plan_{user_id}.pdf"
    pdf.output(filename)
    return filename

# --- ХЭНДЛЕРЫ ---

@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    all_users.add(user_id)
    user_history[user_id] = []
    
    await message.answer(
        "🥊 В углу ринга! Я твой тренер Iron Corner.\n\n"
        "Команды:\n"
        "/getplan - получить план тренировок (PDF)\n\n"
        "Присылай фото еды для анализа или просто пиши — расскажи о себе: вес, возраст, цели?"
    )

@dp.message(Command("getplan"))
async def send_plan(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in user_history or len(user_history[user_id]) == 0:
        await message.answer("⚠️ Сначала расскажи о себе! Вес, возраст, цели...")
        return
    
    await message.answer("Готовлю твой боевой план... ⏳")

    try:
        model = get_model()
        
        # Формируем промпт с системной инструкцией
        full_prompt = f"{SYSTEM_PROMPT}\n\nСформируй итоговый четкий план тренировок и питания на неделю в структурированном виде."
        
        response = model.generate_content(full_prompt)
        
        pdf_path = create_pdf(user_id, response.text)
        document = FSInputFile(pdf_path)
        await message.bot.send_document(
            message.chat.id, 
            document, 
            caption="🏆 Твой план победы!"
        )
        
        os.remove(pdf_path)
        
    except Exception as e:
        await message.answer(f"❌ Сбой: {str(e)}")

@dp.message(Command("stats"))
async def admin_stats(message: types.
Message):
    if message.from_user.id == ADMIN_ID:
        total_messages = sum(len(h) for h in user_history.values())
        await message.answer(
            f"📊 **Статистика:**\n"
            f"Всего бойцов: {len(all_users)}\n"
            f"Активных диалогов: {len(user_history)}\n"
            f"Всего сообщений: {total_messages}"
        )

@dp.message(Command("broadcast"))
async def admin_broadcast(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("Где текст? Пиши: /broadcast Текст")
        return
    
    count = 0
    for uid in all_users:
        try:
            await bot.send_message(uid, f"📢 **ТРЕНЕР НА СВЯЗИ:**\n{text}")
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
            
    await message.answer(f"✅ Отправлено {count} из {len(all_users)} бойцам")

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    all_users.add(user_id)
    
    await message.answer("🧐 Анализирую фото...")
    
    try:
        # Скачиваем фото
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        photo_file = await bot.download_file(file_info.file_path)
        
        # Открываем как PIL Image
        image = Image.open(photo_file)
        
        if user_id not in user_history:
            user_history[user_id] = []
        
        model = get_model()
        
        prompt = f"{SYSTEM_PROMPT}\n\nПроанализируй это фото как тренер по боксу. Если это еда - оцени КБЖУ и калорийность. Если техника - дай рекомендации."
        
        response = model.generate_content([prompt, image])
        
        await message.reply(response.text)
        
    except Exception as e:
        await message.reply(f"❌ Ошибка: {str(e)}\nПопробуй другое фото.")

@dp.message()
async def chat_text(message: types.Message):
    user_id = message.from_user.id
    all_users.add(user_id)
    
    if user_id not in user_history:
        user_history[user_id] = []

    try:
        model = get_model()
        
        # Добавляем системный промпт к запросу
        full_prompt = f"{SYSTEM_PROMPT}\n\n{message.text}"
        
        response = model.generate_content(full_prompt)
        
        await message.reply(response.text)
        
    except Exception as e:
        await message.reply(f"❌ Ошибка Gemini: {str(e)}")

# --- ЗАПУСК ---
async def main():
    print("🥊 Iron Corner бот запущен!")
    await dp.start_polling(bot)

if name == "__main__":
    asyncio.run(main())
