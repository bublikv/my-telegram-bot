# main.py — всё в одном файле
# aiogram 3.x

import os
import asyncio
import logging
import datetime
from typing import Optional, Literal

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, ChatJoinRequest
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

import aiosqlite
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
def get_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="➕ Добавить бота в канал",
                url="https://t.me/sub1_check_bot?startchannel=true&admin=invite_users"
            )
        ]
    ])
    return keyboard

# ---------------------- CONFIG ----------------------
TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = "subbot.db"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ---------------------- DB LAYER ----------------------
CREATE_TABLES_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS campaigns (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id            INTEGER NOT NULL,
    main_chat_id        TEXT    NOT NULL, -- chat_id основного канала
    main_name           TEXT,
    main_username       TEXT,
    main_join_link      TEXT,             -- ссылку делаем join-request (creates_join_request=1)
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL,
    chat_id         TEXT    NOT NULL,
    username        TEXT,
    name            TEXT,
    invite_link     TEXT    -- обычная ссылка без join request: на подписку
);

CREATE TABLE IF NOT EXISTS links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    url             TEXT    NOT NULL
);

-- Порядок обязателен: position растёт по мере добавления
CREATE TABLE IF NOT EXISTS campaign_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER NOT NULL,
    item_type       TEXT    NOT NULL,  -- 'channel' | 'link'
    ref_id          INTEGER NOT NULL,  -- FK -> channels.id или links.id (в зависимости от item_type)
    position        INTEGER NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
);
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES_SQL)
        await db.commit()
# --- Users ---
async def db_add_users_table_once():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY
            )
        """)
        await db.commit()
async def db_add_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            # если пользователь уже есть — ничего не делаем
            pass

async def db_get_users() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
        return [r["user_id"] for r in rows]

async def db_user_exists(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE user_id=? LIMIT 1", (user_id,))
        return await cur.fetchone() is not None
# --- Campaigns ---
async def db_create_campaign(owner_id: int, main_chat_id: str, main_name: str, main_username: Optional[str], main_join_link: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.datetime.utcnow().isoformat()
        cur = await db.execute(
            "INSERT INTO campaigns (owner_id, main_chat_id, main_name, main_username, main_join_link, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, str(main_chat_id), main_name, main_username, main_join_link, now)
        )
        await db.commit()
        return cur.lastrowid

async def db_get_campaign(campaign_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def db_get_campaign_by_main_chat(main_chat_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM campaigns WHERE main_chat_id=?", (str(main_chat_id),))
        row = await cur.fetchone()
        return dict(row) if row else None

async def db_list_campaigns_by_owner(owner_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, main_name, main_username, main_chat_id, created_at FROM campaigns WHERE owner_id=? ORDER BY id DESC",
            (owner_id,)
        )
        return [dict(r) for r in await cur.fetchall()]

# --- Channels/Links ---
async def db_insert_channel(owner_id: int, chat_id: str, name: str, username: Optional[str], invite_link: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO channels (owner_id, chat_id, name, username, invite_link) VALUES (?, ?, ?, ?, ?)",
            (owner_id, str(chat_id), name, username, invite_link)
        )
        await db.commit()
        return cur.lastrowid

async def db_insert_link(owner_id: int, name: str, url: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO links (owner_id, name, url) VALUES (?, ?, ?)",
            (owner_id, name, url)
        )
        await db.commit()
        return cur.lastrowid

async def db_get_channel(channel_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM channels WHERE id=?", (channel_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def db_get_link(link_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM links WHERE id=?", (link_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def db_update_channel_name(channel_id: int, new_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE channels SET name=? WHERE id=?", (new_name, channel_id))
        await db.commit()

async def db_update_channel_link(channel_id: int, new_link: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE channels SET invite_link=? WHERE id=?", (new_link, channel_id))
        await db.commit()

async def db_update_link_url(link_id: int, new_url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE links SET url=? WHERE id=?", (new_url, link_id))
        await db.commit()

# --- Campaign Items ---
async def db_add_campaign_item(campaign_id: int, item_type: Literal["channel", "link"], ref_id: int, position: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO campaign_items (campaign_id, item_type, ref_id, position) VALUES (?, ?, ?, ?)",
            (campaign_id, item_type, ref_id, position)
        )
        await db.commit()

async def db_get_campaign_items(campaign_id: int) -> list[dict]:
    """
    Возвращает нормализованный список с сохранением порядка.
    Элемент = {
        'type': 'channel'|'link',
        'name': str,
        'invite_link' (для channel) / 'url' (для link),
        'chat_id' (для channel) / None,
        'username' (для channel) / None
    }
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT item_type, ref_id, position FROM campaign_items WHERE campaign_id=? ORDER BY position ASC",
            (campaign_id,)
        )
        rows = await cur.fetchall()

        result: list[dict] = []
        for r in rows:
            if r["item_type"] == "channel":
                cur2 = await db.execute("SELECT * FROM channels WHERE id=?", (r["ref_id"],))
                c = await cur2.fetchone()
                if c:
                    c = dict(c)
                    result.append({
                        "type": "channel",
                        "name": c["name"],
                        "invite_link": c["invite_link"],
                        "chat_id": c["chat_id"],
                        "username": c["username"]
                    })
            else:
                cur3 = await db.execute("SELECT * FROM links WHERE id=?", (r["ref_id"],))
                lnk = await cur3.fetchone()
                if lnk:
                    lnk = dict(lnk)
                    result.append({
                        "type": "link",
                        "name": lnk["name"],
                        "url": lnk["url"]
                    })
        return result
# --- DB updates ---
async def db_update_campaign(campaign_id: int, main_chat_id: str, main_name: str, main_username: Optional[str], main_join_link: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE campaigns SET main_chat_id=?, main_name=?, main_username=?, main_join_link=? WHERE id=?",
            (str(main_chat_id), main_name, main_username, main_join_link, campaign_id)
        )
        await db.commit()

async def db_clear_campaign_items(campaign_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM campaign_items WHERE campaign_id=?", (campaign_id,))
        await db.commit()

# ---------------------- UTILS ----------------------
def is_valid_channel_id(text: str) -> bool:
    return text.startswith("-100") and text[4:].isdigit()

async def is_subscribed(user_id: int, channel_id: str) -> bool:
    """
    Проверка подписки на канал/чат: возвращает True если участник не 'left'/'kicked'.
    Для приватных каналов бот должен быть участником/админом.
    """
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status not in ("left", "kicked")
    except (TelegramBadRequest, TelegramAPIError):
        return False
    except Exception:
        return False

async def make_invite_link(chat_id: int, join_request: bool) -> str:
    """
    Создать ссылку приглашения:
    - join_request=True -> ссылка на запрос на вступление в основной канал (без прямого захода);
    - join_request=False -> обычная бесконечная ссылка для подписки (secondary).
    """
    link = await bot.create_chat_invite_link(
        chat_id=chat_id,
        name="generated",
        expire_date=None,
        member_limit=None,
        creates_join_request=join_request
    )
    return link.invite_link

def add_bot_to_channel_markup(bot_username: str) -> InlineKeyboardMarkup:
    url = "https://t.me/sub1_check_bot?startchannel=true&admin=invite_users"
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ Добавить бота в канал", url=url))
    return kb.as_markup()


# ---------------------- FSM ----------------------
class OwnerFlow(StatesGroup):
    waiting_for_main_channel_input = State()
    waiting_for_secondary_channel_input = State()
    waiting_for_link_name = State()
    waiting_for_link_url = State()
    waiting_for_channel_rename = State()
    waiting_for_channel_link_update = State()
    waiting_for_main_rename = State()
    waiting_for_main_link_update = State()
    waiting_for_link_url_update = State()


# ---------------------- DRAFT STORAGE (FSM) ----------------------
# В драфте храним всё до "Готово", затем записываем в БД.
# draft = {
#   'main': {chat_id, name, username, join_link},
#   'items': [ {'type':'channel', 'name','chat_id','username','invite_link'},
#              {'type':'link', 'name','url'} ]
# }

async def get_draft(state: FSMContext) -> dict:
    data = await state.get_data()
    return data.get("draft", {"main": None, "items": []})

async def set_draft(state: FSMContext, draft: dict):
    await state.update_data(draft=draft)

async def reset_draft(state: FSMContext):
    await state.update_data(draft={"main": None, "items": []})
# --- Draft loader for edit mode ---

async def load_campaign_to_draft(state: FSMContext, campaign_id: int):
    campaign = await db_get_campaign(campaign_id)
    if not campaign:
        return False

    items = await db_get_campaign_items(campaign_id)
    draft = {
        "main": {
            "chat_id": str(campaign["main_chat_id"]),
            "name": campaign["main_name"],
            "username": campaign.get("main_username"),
            "join_link": campaign["main_join_link"]
        },
        "items": items[:]  # уже нормализованные элементы
    }
    await state.update_data(draft=draft, edit_campaign_id=campaign_id)
    return True

# ---------------------- OWNER MENUS ----------------------
def pretty_item_title(item: dict) -> str:
    if item["type"] == "channel":
        base = item["name"] or (item.get("username") or item.get("chat_id"))
        return f"Канал для подписки: {base}"
    else:
        return f"Ссылка: {item['name']}"

async def build_owner_edit_menu(draft: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # Кнопка редактирования основного канала
    if draft["main"]:
        main_title = draft["main"]["name"] or (draft["main"].get("username") or draft["main"]["chat_id"])
        kb.row(InlineKeyboardButton(text=f"🎯 Редактировать основной канал: «{main_title}»", callback_data="edit_main"))
    else:
        kb.row(InlineKeyboardButton(text="🎯 Выбрать основной канал", callback_data="owner_add_main"))

    # Динамический список элементов (каналы/ссылки) в порядке добавления
    if draft["items"]:
        for idx, item in enumerate(draft["items"]):
            kb.row(
                InlineKeyboardButton(text=f"⚙️ {pretty_item_title(item)}", callback_data=f"edit_item_{idx}")
            )
    else:
        kb.row(InlineKeyboardButton(text="— список пуст —", callback_data="noop"))

    # Действия
    kb.row(
        InlineKeyboardButton(text="➕ Добавить канал для подписки", callback_data="owner_add_secondary"),
        InlineKeyboardButton(text="🔗 Я хочу добавить ссылку", callback_data="owner_add_link")
    )
    kb.row(
        InlineKeyboardButton(text="✅ Готово", callback_data="owner_finalize"),
        InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_start")
    )
    return kb.as_markup()

async def build_edit_item_menu(idx: int, item: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if item["type"] == "channel":
        kb.row(InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"rename_ch_{idx}"))
        kb.row(InlineKeyboardButton(text="🔗 Обновить ссылку", callback_data=f"relink_ch_{idx}"))
    else:
        kb.row(InlineKeyboardButton(text="🔗 Обновить URL", callback_data=f"relink_link_{idx}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_owner_menu"))
    return kb.as_markup()

async def build_edit_main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✏️ Изменить название", callback_data="rename_main"))
    kb.row(InlineKeyboardButton(text="🔗 Пересоздать join-request ссылку", callback_data="relink_main"))
    kb.row(InlineKeyboardButton(text="🗑️ Удалить основной канал", callback_data="drop_main"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_owner_menu"))
    return kb.as_markup()


# ---------------------- START & OWNER FLOW ----------------------
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    print(message.from_user.id)
    statee = await db_add_user(message.from_user.id)
    if statee is True:
        await bot.send_message('1418452797', text=f'Новый пользователь! ID: {message.from_user.id}\n@{message.from_user.username}')
        await bot.send_message('1834505941', text=f'Новый пользователь! ID: {message.from_user.id}\n@{message.from_user.username}')

    #args = (command.args or "").strip()
    args=234
    # deeplink для подписчика: /start join_<campaign_id> (оставляем логику как есть)
    #if args.startswith("join_"):
    if args==111:
        try:
            campaign_id = int(args.split("_", 1)[1])
        except Exception:
            await message.answer("❌ Неверная ссылка. Попробуй ещё раз.")
            return

        campaign = await db_get_campaign(campaign_id)
        if not campaign:
            await message.answer("❌ Кампания не найдена или уже неактуальна.")
            return

        items = await db_get_campaign_items(campaign_id)
        text = (
            "<b>Проверка подписки</b>\n\n"
            "1) Подпишись на каналы ниже и перейди по ссылкам.\n"
            "2) Нажми <b>✅ Я подписался</b> — проверю и одобрю заявку на вступление."
        )
        kb = InlineKeyboardBuilder()
        for it in items:
            if it["type"] == "channel":
                url = it.get("invite_link") or (f"https://t.me/{it['username']}" if it.get("username") else None)
                title = it.get("name") or it.get("username") or it.get("chat_id")
                if url:
                    kb.row(InlineKeyboardButton(text=f"🔔 Подписаться: {title}", url=url))
            else:
                kb.row(InlineKeyboardButton(text=f"🌐 Перейти: {it['name']}", url=it["url"]))
        kb.row(InlineKeyboardButton(text="✅ Я подписался", callback_data=f"user_check_{campaign_id}"))
        await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        return

    # --- Главное меню владельца ---
    await state.clear()
    await reset_draft(state)

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🚀 Создать кампанию", callback_data="owner_new_campaign"))
    kb.row(InlineKeyboardButton(text="📁 Мои кампании", callback_data="owner_my_campaigns"))

    await message.answer(
        "<b>Привет!</b>\n"
        "Здесь ты настраиваешь доступ к своему каналу: обязательные шаги и авто-одобрение заявок.\n\n"
        "• Нажми «Создать кампанию», чтобы собрать список шагов.\n"
        "• Или зайди в «Мои кампании», чтобы отредактировать существующие.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_to_start")
async def back_to_start(cb: types.CallbackQuery, state: FSMContext):
    await start_cmd(cb, state)

@dp.callback_query(F.data == "owner_new_campaign")
async def owner_new_campaign(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await reset_draft(state)               # сбрасываем драфт
    await state.update_data(edit_campaign_id=None)  # явно выходим из режима редактирования

    binfo = await bot.get_me()
    add_bot_kb = add_bot_to_channel_markup(bot_username=f"@{binfo.username}")

    kb = InlineKeyboardBuilder()
    kb.row(*add_bot_kb.inline_keyboard[0])
    kb.row(InlineKeyboardButton(text="✍️ Указать основной канал", callback_data="owner_add_main"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_start"))

    await cb.message.edit_text(
        "<b>Шаг 1 из 2. Основной канал</b>\n\n"
        "1) Добавь бота админом в канал (право создавать приглашения/одобрять заявки).\n"
        "2) Нажми «Указать основной канал» и отправь ID, @username или перешли сообщение из канала.\n\n"
        "Ссылку-приглашение (с заявкой на вступление) выдам в самом конце — после кнопки «Готово».",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await cb.answer()

@dp.callback_query(F.data == "owner_add_main")
async def owner_add_main(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer(
        "📩 Отправь ID канала (например, <code>-1001234567890</code>), <code>@username</code> или перешли сообщение из этого канала.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏪ Отмена", callback_data="back_to_start")]
        ])
    )
    await state.set_state(OwnerFlow.waiting_for_main_channel_input)
    await cb.answer()

@dp.message(OwnerFlow.waiting_for_main_channel_input)
async def owner_receive_main_channel(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    chat_id = None
    username = None
    title = None

    # 1) пересланное
    if message.forward_from_chat and getattr(message.forward_from_chat, "type", None) in ("channel", "supergroup"):
        ch = message.forward_from_chat
        chat_id = ch.id
        username = ch.username
        title = ch.title
    else:
        text = (message.text or "").strip()
        if not text:
            await message.reply("❌ Пусто. Отправь ID, @username или перешли сообщение из канала.")
            return
        if is_valid_channel_id(text):
            chat_id = int(text)
        elif text.startswith("@"):
            try:
                ch = await bot.get_chat(text)
                chat_id = ch.id
                username = ch.username
                title = ch.title
            except Exception as e:
                await message.reply(f"❌ Не удалось получить канал по username: {e}")
                return
        else:
            await message.reply("❌ Неверный формат. Нужен ID, @username или пересланное сообщение.")
            return

        if title is None or username is None:
            try:
                ch2 = await bot.get_chat(chat_id)
                username = username or ch2.username
                title = title or ch2.title
            except Exception:
                pass

    # проверяем права бота
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
        if member.status not in ("administrator", "creator"):
            await message.reply("❗ Бот не админ в этом канале. Выдай права администратора и повтори.")
            return
    except Exception as e:
        await message.reply(f"❌ Не удалось проверить права бота: {e}")
        return

    # создаём JOIN-REQUEST ссылку для основного канала
    try:
        join_link = await make_invite_link(chat_id=chat_id, join_request=True)
    except Exception as e:
        await message.reply(f"❌ Не удалось создать join-request ссылку: {e}")
        return

    draft = await get_draft(state)
    draft["main"] = {
        "chat_id": str(chat_id),
        "name": title or f"Канал {chat_id}",
        "username": username,
        "join_link": join_link
    }
    await state.clear()
    await set_draft(state, draft)
      # дальнейшие действия через callback-кнопки

    # показать меню редактирования кампании
    kb = await build_owner_edit_menu(draft)
    await message.answer(
        "✅ Основной канал добавлен!\nТеперь добавь каналы для обязательной подписки и/или ссылки.\n"
        "Порядок сохранится именно такой, как ты их добавишь.",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_owner_menu")
async def back_owner_menu(cb: types.CallbackQuery, state: FSMContext):
    draft = await get_draft(state)
    kb = await build_owner_edit_menu(draft)
    await cb.message.edit_text("⚙️ <b>Редактирование кампании</b>\nВыбирай, что изменить или добавь новые элементы.",
                               reply_markup=kb, parse_mode="HTML")
    await cb.answer()

@dp.callback_query(F.data == "edit_main")
async def edit_main(cb: types.CallbackQuery, state: FSMContext):
    draft = await get_draft(state)
    main = draft.get("main")
    if not main:
        await cb.answer("Сначала выбери основной канал.", show_alert=True)
        return
    kb = await build_edit_main_menu()
    title = main["name"] or (main.get("username") or main.get("chat_id"))
    await cb.message.edit_text(f"🎯 <b>Основной канал:</b> {title}\nЧто меняем?",
                               reply_markup=kb, parse_mode="HTML")
    await cb.answer()

@dp.callback_query(F.data == "rename_main")
async def rename_main(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("✍️ Введи новое отображаемое имя основного канала:")
    await state.set_state(OwnerFlow.waiting_for_main_rename)
    await cb.answer()

@dp.message(OwnerFlow.waiting_for_main_rename)
async def process_main_rename(message: types.Message, state: FSMContext):
    new_name = (message.text or "").strip()
    if not new_name:
        await message.reply("Имя не может быть пустым.")
        return
    draft = await get_draft(state)
    if not draft.get("main"):
        await state.clear()
        await message.reply("Основной канал не выбран.")
        return
    draft["main"]["name"] = new_name
    await state.clear()
    await set_draft(state, draft)
    await message.answer("✅ Имя основного канала обновлено.", reply_markup=await build_owner_edit_menu(draft))

@dp.callback_query(F.data == "relink_main")
async def relink_main(cb: types.CallbackQuery, state: FSMContext):
    # пересоздаём join-request ссылку
    draft = await get_draft(state)
    if not draft.get("main"):
        await cb.answer("Основной канал не выбран.", show_alert=True)
        return
    try:
        new_join = await make_invite_link(int(draft["main"]["chat_id"]), join_request=True)
        draft["main"]["join_link"] = new_join
        await set_draft(state, draft)
        await cb.message.answer("🔗 Новая join-request ссылка для основного канала создана.")
        await cb.message.answer(draft["main"]["join_link"])
    except Exception as e:
        await cb.message.answer(f"❌ Не удалось обновить ссылку: {e}")
    await cb.answer()

@dp.callback_query(F.data == "drop_main")
async def drop_main(cb: types.CallbackQuery, state: FSMContext):
    draft = await get_draft(state)
    draft["main"] = None
    draft["items"] = []
    await set_draft(state, draft)
    await cb.message.answer("🗑️ Основной канал удалён из драфта. Начни снова: выбери новый основной канал.")
    await owner_new_campaign(cb, state)
    await cb.answer()

# --- добавление secondary channel ---
@dp.callback_query(F.data == "owner_add_secondary")
async def owner_add_secondary(cb: types.CallbackQuery, state: FSMContext):
    draft = await get_draft(state)
    if not draft.get("main"):
        await cb.answer("Сначала выбери основной канал.", show_alert=True)
        return
    await cb.message.answer(
        "📩 Отправь ID канала (<code>-100...</code>) или <code>@username</code>, либо перешли сообщение из канала.\n"
        "Бот должен быть админом/участником, чтобы проверять подписку.", reply_markup=get_menu_keyboard(), 
        parse_mode="HTML"
    )
    await state.set_state(OwnerFlow.waiting_for_secondary_channel_input)
    await cb.answer()

@dp.message(OwnerFlow.waiting_for_secondary_channel_input)
async def owner_receive_secondary_channel(message: types.Message, state: FSMContext):
    owner_id = message.from_user.id

    chat_id = None
    username = None
    title = None

    if message.forward_from_chat and getattr(message.forward_from_chat, "type", None) in ("channel", "supergroup"):
        ch = message.forward_from_chat
        chat_id = ch.id
        username = ch.username
        title = ch.title
    else:
        text = (message.text or "").strip()
        if not text:
            await message.reply("❌ Пусто. Отправь ID, @username или пересланное сообщение.")
            return
        if is_valid_channel_id(text):
            chat_id = int(text)
        elif text.startswith("@"):
            try:
                ch = await bot.get_chat(text)
                chat_id = ch.id
                username = ch.username
                title = ch.title
            except Exception as e:
                await message.reply(f"❌ Не удалось получить канал по username: {e}")
                return
        else:
            await message.reply("❌ Неверный формат.")
            return

        if title is None or username is None:
            try:
                ch2 = await bot.get_chat(chat_id)
                username = username or ch2.username
                title = title or ch2.title
            except Exception:
                pass

    # проверим, что бот хотя бы участник (лучше — админ)
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
        if member.status not in ("administrator", "creator", "member"):
            await message.reply("❗ Бот не имеет доступа к этому каналу (не участник). Добавь бота и повтори.")
            return
    except Exception as e:
        await message.reply(f"❌ Ошибка доступа к каналу: {e}")
        return

    # обычная бесконечная ссылка (без join-request), чтобы удобно было подписываться
    try:
        invite = await make_invite_link(chat_id=chat_id, join_request=False)
    except Exception as e:
        await message.reply(f"❌ Не удалось создать ссылку: {e}")
        return

    draft = await get_draft(state)
    draft["items"].append({
        "type": "channel",
        "name": title or f"Канал {chat_id}",
        "chat_id": str(chat_id),
        "username": username,
        "invite_link": invite
    })
    await state.clear()
    await set_draft(state, draft)

    await message.answer("✅ Канал для подписки добавлен.", reply_markup=await build_owner_edit_menu(draft))

# --- добавление ссылки ---
@dp.callback_query(F.data == "owner_add_link")
async def owner_add_link(cb: types.CallbackQuery, state: FSMContext):
    draft = await get_draft(state)
    if not draft.get("main"):
        await cb.answer("Сначала выбери основной канал.", show_alert=True)
        return
    await cb.message.answer("🖊️ Введи название ссылки (как увидит его пользователь):")
    await state.set_state(OwnerFlow.waiting_for_link_name)
    await cb.answer()

@dp.message(OwnerFlow.waiting_for_link_name)
async def owner_link_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.reply("Название не может быть пустым.")
        return
    data = await state.get_data()
    data["new_link_name"] = name
    await state.update_data(**data)
    await message.answer("🔗 Теперь отправь сам URL (начиная с http(s)://).")
    await state.set_state(OwnerFlow.waiting_for_link_url)

@dp.message(OwnerFlow.waiting_for_link_url)
async def owner_link_url(message: types.Message, state: FSMContext):
    url = (message.text or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await message.reply("URL должен начинаться с http:// или https://")
        return
    data = await state.get_data()
    name = data.get("new_link_name", "Ссылка")
    draft = await get_draft(state)
    draft["items"].append({
        "type": "link",
        "name": name,
        "url": url
    })
    await state.clear()
    await set_draft(state, draft)
    await state.update_data(new_link_name=None)
    await message.answer("✅ Ссылка добавлена.", reply_markup=await build_owner_edit_menu(draft))

# --- редактирование элементов списка ---
@dp.callback_query(F.data.startswith("edit_item_"))
async def edit_item(cb: types.CallbackQuery, state: FSMContext):
    idx = int(cb.data.split("_", 2)[2])
    draft = await get_draft(state)
    if idx < 0 or idx >= len(draft["items"]):
        await cb.answer("Элемент не найден.", show_alert=True)
        return
    item = draft["items"][idx]
    kb = await build_edit_item_menu(idx, item)
    await cb.message.edit_text(f"⚙️ <b>{pretty_item_title(item)}</b>\nЧто меняем?", reply_markup=kb, parse_mode="HTML")
    await cb.answer()

@dp.callback_query(F.data.startswith("rename_ch_"))
async def rename_channel_request(cb: types.CallbackQuery, state: FSMContext):
    idx = int(cb.data.split("_", 2)[2])
    await state.update_data(rename_idx=idx)
    await cb.message.answer("✍️ Введи новое имя канала (как увидит пользователь):")
    await state.set_state(OwnerFlow.waiting_for_channel_rename)
    await cb.answer()

@dp.message(OwnerFlow.waiting_for_channel_rename)
async def rename_channel_apply(message: types.Message, state: FSMContext):
    new_name = (message.text or "").strip()
    data = await state.get_data()
    idx = data.get("rename_idx", -1)
    draft = await get_draft(state)
    if not new_name:
        await message.reply("Имя не может быть пустым.")
        return
    if idx < 0 or idx >= len(draft["items"]) or draft["items"][idx]["type"] != "channel":
        await state.clear()
        await message.reply("Элемент не найден.")
        return
    draft["items"][idx]["name"] = new_name
    await state.clear()
    await set_draft(state, draft)
    await message.answer("✅ Имя обновлено.", reply_markup=await build_owner_edit_menu(draft))

@dp.callback_query(F.data.startswith("relink_ch_"))
async def relink_channel_request(cb: types.CallbackQuery, state: FSMContext):
    idx = int(cb.data.split("_", 2)[2])
    await state.update_data(relink_idx=idx)
    await cb.message.answer("🔗 Вставь новую ссылку-приглашение для канала:")
    await state.set_state(OwnerFlow.waiting_for_channel_link_update)
    await cb.answer()

@dp.message(OwnerFlow.waiting_for_channel_link_update)
async def relink_channel_apply(message: types.Message, state: FSMContext):
    new_link = (message.text or "").strip()
    data = await state.get_data()
    idx = data.get("relink_idx", -1)
    draft = await get_draft(state)
    if not new_link.startswith("http"):
        await message.reply("Это должна быть ссылка (начинается с http...).")
        return
    if idx < 0 or idx >= len(draft["items"]) or draft["items"][idx]["type"] != "channel":
        await state.clear()
        await message.reply("Элемент не найден.")
        return
    draft["items"][idx]["invite_link"] = new_link
    await state.clear()
    await set_draft(state, draft)
    await message.answer("✅ Ссылка обновлена.", reply_markup=await build_owner_edit_menu(draft))

@dp.callback_query(F.data.startswith("relink_link_"))
async def relink_link_request(cb: types.CallbackQuery, state: FSMContext):
    idx = int(cb.data.split("_", 2)[2])
    await state.update_data(relink_link_idx=idx)
    await cb.message.answer("🔗 Вставь новый URL для этой ссылки:")
    await state.set_state(OwnerFlow.waiting_for_link_url_update)
    await cb.answer()

@dp.message(OwnerFlow.waiting_for_link_url_update)
async def relink_link_apply(message: types.Message, state: FSMContext):
    new_url = (message.text or "").strip()
    data = await state.get_data()
    idx = data.get("relink_link_idx", -1)
    draft = await get_draft(state)
    if not (new_url.startswith("http://") or new_url.startswith("https://")):
        await message.reply("URL должен начинаться с http:// или https://")
        return
    if idx < 0 or idx >= len(draft["items"]) or draft["items"][idx]["type"] != "link":
        await state.clear()
        await message.reply("Элемент не найден.")
        return
    draft["items"][idx]["url"] = new_url
    await state.clear()
    await set_draft(state, draft)
    await message.answer("✅ URL обновлён.", reply_markup=await build_owner_edit_menu(draft))

# --- финализация кампании ---
@dp.callback_query(F.data == "owner_finalize")
async def owner_finalize(cb: types.CallbackQuery, state: FSMContext):
    owner_id = cb.from_user.id
    draft = await get_draft(state)

    if not draft.get("main"):
        await cb.answer("Сначала добавь основной канал.", show_alert=True)
        return

    # создаём записи в БД
    try:
        camp_id = await db_create_campaign(
            owner_id=owner_id,
            main_chat_id=draft["main"]["chat_id"],
            main_name=draft["main"]["name"],
            main_username=draft["main"].get("username"),
            main_join_link=draft["main"]["join_link"]
        )

        # сохраняем элементы с порядком
        pos = 1
        for it in draft["items"]:
            if it["type"] == "channel":
                ch_id = await db_insert_channel(
                    owner_id=owner_id,
                    chat_id=it["chat_id"],
                    name=it["name"],
                    username=it.get("username"),
                    invite_link=it.get("invite_link")
                )
                await db_add_campaign_item(camp_id, "channel", ch_id, pos)
            else:
                link_id = await db_insert_link(owner_id=owner_id, name=it["name"], url=it["url"])
                await db_add_campaign_item(camp_id, "link", link_id, pos)
            pos += 1

        # deep-link для меню подписки
        me = await bot.get_me()
        deep_link = f"https://t.me/{me.username}?start=join_{camp_id}"

        kb = InlineKeyboardBuilder()
        if draft["main"]["join_link"]:
            kb.row(InlineKeyboardButton(text="🎯 Открыть основной канал (join-request)", url=draft["main"]["join_link"]))
        kb.row(InlineKeyboardButton(text="➡️ Открыть меню подписки (бот)", url=deep_link))

        await cb.message.edit_text(
            "<b>✅ Кампания создана!</b>\n\n"
            "1) Размести кнопку <b>Открыть основной канал</b> — пользователи будут отправлять запрос на вступление.\n"
            "2) Дай ссылку <b>Открыть меню подписки</b> — там они увидят, где подписаться и проверят всё одной кнопкой.\n\n"
            "После успешной проверки бот автоматически одобрит join-request.",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )

        await reset_draft(state)
    except Exception as e:
        await cb.message.answer(f"❌ Что-то пошло не так при сохранении кампании: {e}")
    await cb.answer()

# --- мои кампании (просмотр) ---
@dp.callback_query(F.data == "owner_my_campaigns")
async def owner_my_campaigns(cb: types.CallbackQuery):
    rows = await db_list_campaigns_by_owner(cb.from_user.id)
    kb = InlineKeyboardBuilder()
    if not rows:
        kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_start"))
        await cb.message.edit_text("Пока кампаний нет. Нажми «Создать новую кампанию».",
                                   reply_markup=kb.as_markup())
        await cb.answer()
        return
    for r in rows:
        title = r["main_name"] or r.get("main_username") or r.get("main_chat_id")
        kb.row(InlineKeyboardButton(text=f"📌 Кампания #{r['id']}: {title}", callback_data=f"owner_view_c_{r['id']}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_start"))
    await cb.message.edit_text("📁 <b>Мои кампании</b>\nВыбери кампанию для просмотра:",
                               reply_markup=kb.as_markup(), parse_mode="HTML")
    await cb.answer()

@dp.callback_query(F.data.startswith("owner_view_c_"))
async def owner_view_campaign(cb: types.CallbackQuery):
    camp_id = int(cb.data.split("_", 3)[3])
    campaign = await db_get_campaign(camp_id)
    if not campaign:
        await cb.answer("Кампания не найдена.", show_alert=True)
        return
    items = await db_get_campaign_items(camp_id)
    me = await bot.get_me()
    deep_link = f"https://t.me/{me.username}?start=join_{camp_id}"

    text = (
        f"<b>Кампания #{camp_id}</b>\n"
        f"Основной канал: <b>{campaign['main_name']}</b> (id: <code>{campaign['main_chat_id']}</code>)\n"
        f"Создана: {campaign['created_at']}\n\n"
        f"<b>Элементы (по порядку):</b>\n"
    )
    for i, it in enumerate(items, 1):
        if it["type"] == "channel":
            t = it["name"] or it.get("username") or it.get("chat_id")
            text += f"{i}. Канал — {t}\n"
        else:
            text += f"{i}. Ссылка — {it['name']}\n"
    text += f"\n<b>Join-request:</b> {campaign.get('main_join_link') or '—'}\n"
    text += f"<b>Deeplink:</b> {deep_link}\n"

    kb = InlineKeyboardBuilder()
    if campaign.get("main_join_link"):
        kb.row(InlineKeyboardButton(text="🎯 Открыть основной канал", url=campaign["main_join_link"]))
    kb.row(InlineKeyboardButton(text="➡️ Открыть меню подписки", url=deep_link))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="owner_my_campaigns"))
    await cb.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    await cb.answer()


# ---------------------- USER FLOW: CHECK ----------------------
def build_user_check_kb(campaign_id: int, campaign: dict, items: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if campaign.get("main_join_link"):
        None
       #kb.row(InlineKeyboardButton(text=f"🎯 Открыть основной канал: {campaign['main_name']}",
                                    #url=campaign["main_join_link"]))
    for it in items:
        if it["type"] == "channel":
            url = it.get("invite_link") or (f"https://t.me/{it['username']}" if it.get("username") else None)
            title = it.get("name") or it.get("username") or it.get("chat_id")
            if url:
                kb.row(InlineKeyboardButton(text=f"🔔 Подписаться: {title}", url=url))
        else:
            kb.row(InlineKeyboardButton(text=f"🌐 Перейти: {it['name']}", url=it["url"]))
    kb.row(InlineKeyboardButton(text="✅ Я подписался", callback_data=f"user_check_{campaign_id}"))
    return kb.as_markup()

@dp.callback_query(F.data.startswith("user_check_"))
async def user_check(cb: types.CallbackQuery):
    campaign_id = int(cb.data.split("_", 2)[2])
    campaign = await db_get_campaign(campaign_id)
    if not campaign:
        await cb.answer("Кампания не найдена.", show_alert=True)
        return
    items = await db_get_campaign_items(campaign_id)

    # проверяем подписку на все каналы
    missing = []
    for it in items:
        if it["type"] == "channel":
            ok = await is_subscribed(cb.from_user.id, it["chat_id"])
            if not ok:
                missing.append(it)

    if missing:
        text = "<b>Ещё чуть-чуть!</b>\nТы не подписан(а) на:\n"
        for m in missing:
            t = m.get("name") or m.get("username") or m.get("chat_id")
            text += f"• {t}\n"
        text += "\nПосле подписки вернись и нажми <b>✅ Я подписался</b>."
        kb = InlineKeyboardBuilder()
        for m in missing:
            url = m.get("invite_link") or (f"https://t.me/{m['username']}" if m.get("username") else None)
            t = m.get("name") or m.get("username") or m.get("chat_id")
            if url:
                kb.row(InlineKeyboardButton(text=f"🔔 Подписаться: {t}", url=url))
        kb.row(InlineKeyboardButton(text="✅ Проверить снова", callback_data=f"user_check_{campaign_id}"))
        await cb.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        await cb.answer()
        return

    # если всё ок — одобряем join request в основной канал
    try:
        await bot.approve_chat_join_request(chat_id=campaign["main_chat_id"], user_id=cb.from_user.id)
        await cb.message.edit_text("🎉 Готово! Запрос на вступление одобрен — добро пожаловать в основной канал.")
    except TelegramBadRequest:
        # если нет ожидающего запроса — подскажем отправить его
        text = (
            "✅ Подписки проверены — всё чисто.\n"
            "Но я не нашёл от тебя запроса на вступление. Сначала отправь его в основной канал, "
            "а затем жми «Я подписался».\n"
        )
        await cb.message.edit_text(
            text, reply_markup=build_user_check_kb(campaign_id, campaign, items), parse_mode="HTML"
        )
    except Exception as e:
        await cb.message.answer(f"⚠️ Не получилось одобрить запрос автоматически: {e}")
    await cb.answer()


# ---------------------- JOIN REQUEST HANDLER ----------------------
@dp.chat_join_request()
async def on_join_request(evt: ChatJoinRequest):
    """
    Когда пользователь отправляет запрос на вступление в основной канал — показываем ему чек-лист.
    """
    statee= await db_add_user(evt.from_user.id)
    if statee == True:
        if evt.from_user.username:
            username = evt.from_user.username
        else:
            username = "Отсутствует"
        await bot.send_message('1418452797', text=f'Новый пользователь! ID: {evt.from_user.id}\n@{evt.from_user.username}')
        await bot.send_message('1834505941', text=f'Новый пользователь! ID: {evt.from_user.id}\n@{evt.from_user.username}')
    try:
        campaign = await db_get_campaign_by_main_chat(str(evt.chat.id))
        if not campaign:
            # нет кампании для этого канала — ничего не делаем (или можно авто-одобрить/логировать)
            return

        items = await db_get_campaign_items(campaign["id"])
        text = (
            f"👋 Привет, {evt.from_user.full_name}!\n\n"
            "<b>Чтобы мы одобрили твой запрос</b>, подпишись на все каналы ниже и перейди по всем ссылкам. "
            "Затем нажми <b>✅ Я подписался</b> — я проверю и впущу тебя в основной канал."
        )
        kb = build_user_check_kb(campaign["id"], campaign, items)

        # Пытаемся написать пользователю в ЛС.
        # Если пользователь не нажимал /start бота, это может не доставиться.
        await bot.send_message(chat_id=evt.from_user.id, text=text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        # Нельзя инициировать диалог — оставим запрос в ожидании. Пользователь увидит подсказки в описании канала/посте.
        pass
    except Exception:
        pass


# ---------------------- NOOP ----------------------
@dp.callback_query(F.data == "noop")
async def noop(cb: types.CallbackQuery):
    await cb.answer()


# ---------------------- RUN ----------------------
if __name__ == "__main__":
    async def main():
        await init_db()
        await dp.start_polling(bot)

    asyncio.run(main())