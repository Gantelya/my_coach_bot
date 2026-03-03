import os
import asyncio
import json
import re
import urllib.request
from datetime import datetime
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from groq import Groq
from fpdf import FPDF
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Фейковый сервер для Railway
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
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
REDIS_URL = os.getenv("REDIS_URL")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")
ADMIN_ID = 5492881784

SYSTEM_PROMPT = """
Ты — "Iron Corner", профессиональный тренер по боксу с 20-летним стажем.
Твоя цель: привести пользователя к пиковой форме.
1. Тренировки: составляй планы (мешок, лапы, бой с тенью, ОФП).
2. Питание: считай КБЖУ, анализируй описание еды.
3. Стиль: мотивирующий, жесткий, но справедливый. Используй сленг (джеб, тайминг).
4. Помни всю историю переписки с пользователем и не предлагай повторно то, что уже обсуждали.
В конце ответа желай "убойного настроя".
"""

NUTRITION_PROMPT = """
Ты — диетолог и тренер по боксу. Пользователь описал что съел.
Оцени примерное КБЖУ (калории, белки, жиры, углеводы).
Скажи подходит ли это питание для боксёра.
Дай короткий совет. Будь конкретным и кратким.
Формат ответа:
🍽 Что съел: [повтори]
🔥 Калории: примерно X ккал
💪 Белки: X г | Жиры: X г | Углеводы: X г
✅/❌ Оценка: [подходит/не подходит для бойца]
💡 Совет: [1-2 предложения]
"""

PROGRESS_PROMPT = """
Ты — тренер по боксу. Проанализируй прогресс бойца за неделю.
Сравни текущие показатели с предыдущими если есть.
Дай мотивацию и конкретный совет на следующую неделю.
Будь жёстким но справедливым тренером.
"""

# Инициализация
client = Groq(api_key=GROQ_API_KEY)
bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
redis_client = None

# --- FSM СОСТОЯНИЯ ---

class ReminderSetup(StatesGroup):
    waiting_for_time = State()

class ProgressInput(StatesGroup):
    waiting_for_weight = State()
    waiting_for_results = State()

class NutritionInput(StatesGroup):
    waiting_for_food = State()

# --- REDIS ФУНКЦИИ ---

async def get_history(user_id: int):
    try:
        data = await redis_client.get(f"history:{user_id}")
        if data:
            return json.loads(data)
    except:
        pass
    return [{"role": "system", "content": SYSTEM_PROMPT}]

async def save_history(user_id: int, history: list):
    try:
        await redis_client.set(
            f"history:{user_id}",
            json.dumps(history, ensure_ascii=False)
        )
    except:
        pass

async def get_all_users():
    try:
        data = await redis_client.get("all_users")
        if data:
            return set(json.loads(data))
    except:
        pass
    return set()

async def save_all_users(users: set):
    try:
        await redis_client.set("all_users", json.dumps(list(users)))
    except:
        pass

async def get_progress(user_id: int):
    try:
        data = await redis_client.get(f"progress:{user_id}")
        if data:
            return json.loads(data)
    except:
        pass
    return []

async def save_progress(user_id: int, progress: list):
    try:
        await redis_client.set(
            f"progress:{user_id}",
            json.dumps(progress, ensure_ascii=False)
        )
    except:
        pass

async def get_nutrition_log(user_id: int):
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        data = await redis_client.get(f"nutrition:{user_id}:{today}")
        if data:
            return json.loads(data)
    except:
        pass
    return []

async def save_nutrition_log(user_id: int, log: list):
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        await redis_client.set(
            f"nutrition:{user_id}:{today}",
            json.dumps(log, ensure_ascii=False),
            ex=86400
        )
    except:
        pass

async def get_reminder_time(user_id: int):
    try:
        data = await redis_client.get(f"reminder:{user_id}")
        if data:
            return data
    except:
        pass
    return None

async def save_reminder_time(user_id: int, time_str: str):
    try:
        await redis_client.set(f"reminder:{user_id}", time_str)
    except:
        pass

async def delete_reminder(user_id: int):
    try:
        await redis_client.delete(f"reminder:{user_id}")
    except:
        pass

# --- AI ФУНКЦИЯ ---

async def get_ai_response(messages, retries=3):
    for attempt in range(retries):
        try:
            completion = await asyncio.to_thread(
                client.chat.completions.create,
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.7,
                max_tokens=2000
            )
            return completion.choices[0].message.content
        except Exception as e:
            if attempt == retries - 1:
                return f"Ошибка AI после {retries} попыток: {str(e)}"
            await asyncio.sleep((attempt + 1) * 2)

# --- PDF С ПОДДЕРЖКОЙ КИРИЛЛИЦЫ ---

def create_pdf(user_id, text):
    font_path = "DejaVuSans.ttf"

    # Скачиваем шрифт с поддержкой кириллицы если его нет
    if not os.path.exists(font_path):
        try:
            urllib.request.urlretrieve(
                "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf",
                font_path
            )
            print("✅ Шрифт DejaVuSans скачан!")
        except Exception as e:
            print(f"❌ Не удалось скачать шрифт: {e}")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Используем скачанный шрифт или запасной вариант
    if os.path.exists(font_path):
        pdf.add_font("DejaVu", "", font_path)
        pdf.set_font("DejaVu", size=12)
    else:
        # Запасной вариант — убираем кириллицу
        pdf.set_font("Helvetica", size=12)
        text = text.encode("ascii", "ignore").decode("ascii")

    for line in text.split("\n"):
        try:
            pdf.multi_cell(0, 10, txt=line)
        except Exception:
            pass

    filename = f"plan_{user_id}.pdf"
    pdf.output(filename)
    return filename

# --- ФОНОВАЯ ЗАДАЧА: НАПОМИНАНИЯ ---

async def reminder_loop():
    while True:
        try:
            now = datetime.now().strftime("%H:%M")
            users = await get_all_users()
            for user_id in users:
                reminder_time = await get_reminder_time(int(user_id))
                if reminder_time == now:
                    try:
                        await bot.send_message(
                            int(user_id),
                            "🥊 БОЕЦ, ВРЕМЯ ТРЕНИРОВКИ!\n\n"
                            "Ты поставил напоминание на это время.\n"
                            "Вставай и работай — никаких отмазок!\n\n"
                            "Напиши мне и я скажу что делать сегодня 💪"
                        )
                    except:
                        pass
        except:
            pass
        await asyncio.sleep(60)

# --- КЛАВИАТУРЫ ---

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏋️ Тренировка"), KeyboardButton(text="🍽 Дневник питания")],
            [KeyboardButton(text="📊 Мой прогресс"), KeyboardButton(text="⏰ Напоминание")],
            [KeyboardButton(text="📄 Получить план PDF")]
        ],
        resize_keyboard=True
    )

# --- ХЭНДЛЕРЫ ---

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    users = await get_all_users()
    users.add(user_id)
    await save_all_users(users)
    await save_history(user_id, [{"role": "system", "content": SYSTEM_PROMPT}])
    await message.answer(
        "🥊 В углу ринга! Я твой тренер Iron Corner.\n\n"
        "Выбирай что тебе нужно 👇",
        reply_markup=main_keyboard()
    )

@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "✅ Отменено. Возвращаю в главное меню.",
        reply_markup=main_keyboard()
    )

@dp.message(Command("reset"))
async def reset(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await save_history(user_id, [{"role": "system", "content": SYSTEM_PROMPT}])
    await message.answer("🔄 История диалога очищена. Начнём заново!", reply_markup=main_keyboard())

# ===== НАПОМИНАНИЯ =====

@dp.message(F.text == "⏰ Напоминание")
async def reminder_menu(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    current = await get_reminder_time(user_id)

    if current:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✏️ Изменить время")],
                [KeyboardButton(text="❌ Отключить напоминание")],
                [KeyboardButton(text="🔙 Назад")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            f"⏰ Напоминание установлено на {current}\n\n"
            "Что хочешь сделать?",
            reply_markup=keyboard
        )
    else:
        await state.set_state(ReminderSetup.waiting_for_time)
        await message.answer(
            "⏰ В какое время напоминать о тренировке?\n\n"
            "Напиши время в формате ЧЧ:ММ\n"
            "Например: 07:00 или 18:30\n\n"
            "Для отмены напиши /cancel",
            reply_markup=ReplyKeyboardRemove()
        )

@dp.message(F.text == "✏️ Изменить время")
async def change_reminder(message: types.Message, state: FSMContext):
    await state.set_state(ReminderSetup.waiting_for_time)
    await message.answer(
        "⏰ Введи новое время в формате ЧЧ:ММ\n"
        "Например: 07:00 или 18:30\n\n"
        "Для отмены напиши /cancel",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(F.text == "❌ Отключить напоминание")
async def disable_reminder(message: types.Message, state: FSMContext):
    await state.clear()
    await delete_reminder(message.from_user.id)
    await message.answer("❌ Напоминание отключено.", reply_markup=main_keyboard())

@dp.message(ReminderSetup.waiting_for_time)
async def set_reminder_time(message: types.Message, state: FSMContext):
    # Ищем время в формате ЧЧ:ММ в любом месте сообщения
    match = re.search(r'\b(\d{1,2}:\d{2})\b', message.text)
    if not match:
        await message.answer(
            "❌ Не нашёл время!\n\n"
            "Напиши просто цифры: 13:00 или 07:30\n"
            "Для отмены напиши /cancel"
        )
        return

    time_text = match.group(1)
    try:
        datetime.strptime(time_text, "%H:%M")
    except ValueError:
        await message.answer(
            "❌ Неверное время!\n\n"
            "Напиши например: 13:00 или 07:30\n"
            "Для отмены напиши /cancel"
        )
        return

    await save_reminder_time(message.from_user.id, time_text)
    await state.clear()
    await message.answer(
        f"✅ Буду напоминать о тренировке каждый день в {time_text} 🥊",
        reply_markup=main_keyboard()
    )

@dp.message(F.text == "🔙 Назад")
async def go_back(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню 👇", reply_markup=main_keyboard())

# ===== ПРОГРЕСС ТРЕКЕР =====

@dp.message(F.text == "📊 Мой прогресс")
async def progress_menu(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    progress = await get_progress(user_id)

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Записать прогресс")],
            [KeyboardButton(text="📈 Показать историю")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )

    if progress:
        last = progress[-1]
        await message.answer(
            f"📊 Последняя запись ({last['date']}):\n"
            f"⚖️ Вес: {last['weight']} кг\n"
            f"📝 Результаты: {last['results']}\n\n"
            f"Всего записей: {len(progress)}",
            reply_markup=keyboard
        )
    else:
        await message.answer(
            "📊 Прогресс трекер\n\n"
            "Записей пока нет. Начни отслеживать свой прогресс!",
            reply_markup=keyboard
        )

@dp.message(F.text == "➕ Записать прогресс")
async def add_progress_start(message: types.Message, state: FSMContext):
    await state.set_state(ProgressInput.waiting_for_weight)
    await message.answer(
        "⚖️ Введи свой текущий вес в кг\n"
        "Например: 75.5\n\n"
        "Для отмены напиши /cancel",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(ProgressInput.waiting_for_weight)
async def progress_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer(
            "❌ Введи число!\n"
            "Например: 75.5\n\n"
            "Для отмены напиши /cancel"
        )
        return

    await state.update_data(weight=weight)
    await state.set_state(ProgressInput.waiting_for_results)
    await message.answer(
        "💪 Теперь опиши свои результаты за эту неделю:\n\n"
        "Например: провёл 3 тренировки, улучшил скорость удара, "
        "пробежал 5 км, сделал 100 отжиманий\n\n"
        "Для отмены напиши /cancel"
    )

@dp.message(ProgressInput.waiting_for_results)
async def progress_results(message: types.Message, state: FSMContext):
    data = await state.get_data()
    weight = data['weight']
    results = message.text
    user_id = message.from_user.id

    today = datetime.now().strftime("%d.%m.%Y")
    progress = await get_progress(user_id)

    new_entry = {
        "date": today,
        "weight": weight,
        "results": results
    }
    progress.append(new_entry)

    if len(progress) > 12:
        progress = progress[-12:]

    await save_progress(user_id, progress)
    await state.clear()

    await message.answer("🤖 Анализирую твой прогресс...", reply_markup=main_keyboard())

    progress_text = f"Текущие данные бойца:\nДата: {today}\nВес: {weight} кг\nРезультаты: {results}\n\n"
    if len(progress) > 1:
        prev = progress[-2]
        weight_diff = weight - prev['weight']
        diff_text = f"+{weight_diff:.1f}" if weight_diff > 0 else f"{weight_diff:.1f}"
        progress_text += f"Предыдущая запись ({prev['date']}):\nВес: {prev['weight']} кг ({diff_text} кг)\nРезультаты: {prev['results']}"

    ai_response = await get_ai_response([
        {"role": "system", "content": PROGRESS_PROMPT},
        {"role": "user", "content": progress_text}
    ])

    await message.answer(f"📊 Анализ прогресса:\n\n{ai_response}")

@dp.message(F.text == "📈 Показать историю")
async def show_progress_history(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    progress = await get_progress(user_id)

    if not progress:
        await message.answer("📈 История пуста. Начни записывать прогресс!", reply_markup=main_keyboard())
        return

    text = "📈 ИСТОРИЯ ПРОГРЕССА:\n\n"
    for i, entry in enumerate(progress, 1):
        text += f"{i}. {entry['date']}\n"
        text += f"   ⚖️ Вес: {entry['weight']} кг\n"
        text += f"   📝 {entry['results']}\n\n"

    await message.answer(text, reply_markup=main_keyboard())

# ===== ДНЕВНИК ПИТАНИЯ =====

@dp.message(F.text == "🍽 Дневник питания")
async def nutrition_menu(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    today_log = await get_nutrition_log(user_id)
    today = datetime.now().strftime("%d.%m.%Y")

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🍗 Добавить приём пищи")],
            [KeyboardButton(text="📋 Что съел сегодня")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        f"🍽 Дневник питания на {today}\n\n"
        f"Записей сегодня: {len(today_log)}\n\n"
        "Что хочешь сделать?",
        reply_markup=keyboard
    )

@dp.message(F.text == "🍗 Добавить приём пищи")
async def add_meal_start(message: types.Message, state: FSMContext):
    await state.set_state(NutritionInput.waiting_for_food)
    await message.answer(
        "🍗 Опиши что ты съел:\n\n"
        "Например: куриная грудка 200г, гречка 150г, огурец, стакан воды\n\n"
        "Для отмены напиши /cancel",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(NutritionInput.waiting_for_food)
async def process_meal(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    food_text = message.text

    await state.clear()
    await message.answer("🤖 Считаю КБЖУ...", reply_markup=main_keyboard())

    ai_response = await get_ai_response([
        {"role": "system", "content": NUTRITION_PROMPT},
        {"role": "user", "content": food_text}
    ])

    now = datetime.now().strftime("%H:%M")
    log = await get_nutrition_log(user_id)
    log.append({
        "time": now,
        "food": food_text,
        "analysis": ai_response
    })
    await save_nutrition_log(user_id, log)
    await message.answer(ai_response)

@dp.message(F.text == "📋 Что съел сегодня")
async def show_today_nutrition(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    log = await get_nutrition_log(user_id)
    today = datetime.now().strftime("%d.%m.%Y")

    if not log:
        await message.answer(
            f"📋 {today} — записей нет.\nДобавь первый приём пищи!",
            reply_markup=main_keyboard()
        )
        return

    text = f"📋 Питание за {today}:\n\n"
    for i, entry in enumerate(log, 1):
        text += f"🕐 {entry['time']} — {entry['food']}\n\n"

    await message.answer(text, reply_markup=main_keyboard())

# ===== ТРЕНИРОВКА =====

@dp.message(F.text == "🏋️ Тренировка")
async def training_menu(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    history = await get_history(user_id)

    await message.answer("💪 Готовлю тренировку на сегодня...", reply_markup=main_keyboard())

    history.append({
        "role": "user",
        "content": "Дай мне тренировку на сегодня. Учти мои данные и историю наших тренировок."
    })

    response = await get_ai_response(history)
    history.append({"role": "assistant", "content": response})

    if len(history) > 21:
        history = [history[0]] + history[-20:]

    await save_history(user_id, history)
    await message.answer(response)

# ===== ПЛАН PDF =====

@dp.message(F.text == "📄 Получить план PDF")
async def send_plan_button(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    history = await get_history(user_id)

    if len(history) <= 1:
        await message.answer("⚠️ Сначала расскажи о себе! Напиши свой вес, возраст и цели.")
        return

    await message.answer("Готовлю твой боевой план... ⏳")
    try:
        plan_messages = history.copy()
        plan_messages.append({
            "role": "user",
            "content": "Сформируй итоговый четкий план тренировок и питания на неделю."
        })
        response_text = await get_ai_response(plan_messages)
        pdf_path = await asyncio.to_thread(create_pdf, user_id, response_text)
        document = FSInputFile(pdf_path)
        await message.bot.send_document(message.chat.id, document, caption="🏆 Твой план победы!")
        os.remove(pdf_path)
    except Exception as e:
        await message.answer(f"❌ Сбой: {str(e)}")

# ===== КОМАНДЫ АДМИНА =====

@dp.message(Command("stats"))
async def admin_stats(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        users = await get_all_users()
        await message.answer(
            f"📊 Статистика:\n"
            f"Всего бойцов: {len(users)}\n"
        )

@dp.message(Command("broadcast"))
async def admin_broadcast(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("Где текст? Пиши: /broadcast Текст")
        return
    users = await get_all_users()
    count = 0
    for uid in users:
        try:
            await bot.send_message(uid, f"📢 ТРЕНЕР НА СВЯЗИ:\n{text}")
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"✅ Отправлено {count} из {len(users)} бойцам")

# ===== ФОТО =====

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    users = await get_all_users()
    users.add(user_id)
    await save_all_users(users)
    await message.answer(
        "📸 Анализ фото пока недоступен.\n"
        "Используй кнопку 🍽 Дневник питания и опиши словами что ел!"
    )

# ===== ОБЫЧНЫЙ ЧАТ =====

@dp.message()
async def chat_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return

    user_id = message.from_user.id
    username = message.from_user.username or "без username"
    first_name = message.from_user.first_name or ""

    users = await get_all_users()
    users.add(user_id)
    await save_all_users(users)

    history = await get_history(user_id)

    try:
        history.append({"role": "user", "content": message.text})

        if LOG_CHANNEL_ID:
            try:
                await bot.send_message(
                    int(LOG_CHANNEL_ID),
                    f"👤 {first_name} @{username}\n🆔 {user_id}\n💬 {message.text}"
                )
            except:
                pass

        response_text = await get_ai_response(history)
        history.append({"role": "assistant", "content": response_text})

        if len(history) > 21:
            history = [history[0]] + history[-20:]

        await save_history(user_id, history)
        await message.reply(response_text)

    except Exception as e:
        await message.reply(f"❌ Ошибка: {str(e)}")

# --- ЗАПУСК ---
async def main():
    global redis_client
    redis_client = await aioredis.from_url(
        REDIS_URL,
        encoding="utf-8",
        decode_responses=True
    )
    print("✅ Redis подключён!")
    print("🥊 Iron Corner бот запущен с Groq AI!")
    asyncio.create_task(reminder_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())