import os
import asyncio
import io
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
from openai import OpenAI
from fpdf import FPDF
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import base64

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

# Настройки
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
XAI_API_KEY = os.getenv("XAI_API_KEY")  # Новый ключ от xAI

SYSTEM_PROMPT = """
Ты — "Iron Corner", профессиональный тренер по боксу с 20-летним стажем.
Твоя цель: привести пользователя к пиковой форме.
1. Тренировки: составляй планы (мешок, лапы, бой с тенью, ОФП).
2. Питание: считай КБЖУ, анализируй фото еды.
3. Стиль: мотивирующий, жесткий, но справедливый. Используй сленг (джеб, тайминг).
4. Если присылают фото еды: оцени калорийность и скажи, подходит ли это бойцу.
В конце ответа желай "убойного настроя".
"""

# Инициализация клиента Grok
client = OpenAI(
    api_key=XAI_API_KEY,
    base_url="https://api.x.ai/v1"
)

ADMIN_ID = 5492881784 

user_history = {}
all_users = set()

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# --- ФУНКЦИИ ---

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
        try:
            pdf.multi_cell(0, 10, txt=line)
        except:
            pdf.multi_cell(0, 10, txt=line.encode('latin-1', 'ignore').decode('latin-1'))
    
    filename = f"plan_{user_id}.pdf"
    pdf.output(filename)
    return filename

def get_grok_response(messages):
    """Получить ответ от Grok"""
    try:
        completion = client.chat.completions.create(
            model="grok-beta",
            messages=messages,
            temperature=0.7,
            max_tokens=2000
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"Ошибка Grok: {str(e)}"

def encode_image_to_base64(image_bytes):
    """Конвертация изображения в base64"""
    return base64.b64encode(image_bytes).decode('utf-8')

# --- ХЭНДЛЕРЫ ---

@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    all_users.add(user_id)
    user_history[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    await message.answer(
        "🥊 В углу ринга! Я твой тренер Iron Corner.\n\n"
        "Команды:\n"
        "/getplan - получить план тренировок (PDF)\n"
        "/reset - очистить историю диалога\n\n"
        "Присылай фото еды для анализа или просто пиши — расскажи о себе: вес, возраст, цели?"
    )

@dp.message(Command("reset"))
async def reset(message: types.Message):
    user_id = message.from_user.id
    user_history[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    await message.answer("🔄 История диалога очищена. Начнём заново!")

@dp.message(Command("getplan"))
async def send_plan(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in user_history or len(user_history[user_id]) <= 1:
        await message.answer("⚠️ Сначала расскажи о себе! Вес, возраст, цели...")
        return
    
    await message.answer("Готовлю твой боевой план... ⏳")

    try:
        # Создаём сообщения для запроса плана
        plan_messages = user_history[user_id].copy()
        plan_messages.append({
            "role": "user",
            "content": "Сформируй итоговый четкий план тренировок и питания на неделю в структурированном виде."
        })
        
        response_text = get_grok_response(plan_messages)
        
        pdf_path = create_pdf(user_id, response_text)
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
async def admin_stats(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        total_messages = sum(len(h) - 1 for h in user_history.values())  # -1 для system prompt
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
        # Инициализация истории
        if user_id not in user_history:
            user_history[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        # Скачиваем фото
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        photo_bytes = await bot.download_file(file_info.file_path)
        
        # Конвертируем в base64
        image_base64 = encode_image_to_base64(photo_bytes.read())
        
        # Формируем сообщение с изображением
        user_history[user_id].append({
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Проанализируй это фото как тренер по боксу. Если это еда - оцени КБЖУ и калорийность. Если техника - дай рекомендации."
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }
                }
            ]
        })
        
        response_text = get_grok_response(user_history[user_id])
        
        # Сохраняем ответ в историю
        user_history[user_id].append({
            "role": "assistant",
            "content": response_text
        })
        
        await message.reply(response_text)
        
    except Exception as e:
        await message.reply(f"❌ Ошибка: {str(e)}")

@dp.message()
async def chat_text(message: types.Message):
    user_id = message.from_user.id
    all_users.add(user_id)
    
    # Инициализация истории
    if user_id not in user_history:
        user_history[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    try:
        # Добавляем сообщение пользователя
        user_history[user_id].append({
            "role": "user",
            "content": message.text
        })
        
        # Получаем ответ
        response_text = get_grok_response(user_history[user_id])
        
        # Сохраняем ответ
        user_history[user_id].append({
            "role": "assistant",
            "content": response_text
        })
        
        await message.reply(response_text)
        
    except Exception as e:
        await message.reply(f"❌ Ошибка: {str(e)}")

# --- ЗАПУСК ---
async def main():
    print("🥊 Iron Corner бот запущен с Grok AI!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

