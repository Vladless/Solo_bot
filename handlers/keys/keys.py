import locale
from datetime import datetime, timedelta

import asyncpg
from aiogram import Router, types

from auth import login_with_credentials, link_subscription
from bot import bot
from client import add_client, delete_client, extend_client_key
from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL, SERVERS, APP_URL
from database import get_balance, update_balance
from handlers.texts import NO_KEYS
from handlers.texts import key_message, key_relocated
from handlers.texts import RENEWAL_PLANS, INSUFFICIENT_FUNDS_MSG, KEY_NOT_FOUND_MSG, SUCCESS_RENEWAL_MSG, ERROR_RENEWAL_MSG, PLAN_SELECTION_MSG

locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')

router = Router()

@router.callback_query(lambda c: c.data == 'view_keys')
async def process_callback_view_keys(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            records = await conn.fetch('''
                SELECT email, client_id FROM keys WHERE tg_id = $1
            ''', tg_id)

            if records:
                buttons = []
                for record in records:
                    key_name = record['email']
                    client_id = record['client_id']
                    button = types.InlineKeyboardButton(text=f"🔑 {key_name}", callback_data=f'view_key|{key_name}|{client_id}')
                    buttons.append([button])

                back_button = types.InlineKeyboardButton(text='🔙 Назад', callback_data='view_profile')
                buttons.append([back_button])

                inline_keyboard = types.InlineKeyboardMarkup(inline_keyboard=buttons)
                response_message = (
                    "<b>Это ваши устройства:</b>\n\n"
                    "<i>Нажмите на имя устройства для управления его ключом.</i>"
                )

                await bot.delete_message(chat_id=tg_id, message_id=callback_query.message.message_id)

                await bot.send_message(
                    chat_id=tg_id,
                    text=response_message,
                    reply_markup=inline_keyboard,
                    parse_mode="HTML"
                )
            else:
                response_message = NO_KEYS  
                create_key_button = types.InlineKeyboardButton(text='➕ Создать ключ', callback_data='create_key')
                back_button = types.InlineKeyboardButton(text='🔙 Назад', callback_data='view_profile')
                
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[create_key_button], [back_button]])

                await bot.delete_message(chat_id=tg_id, message_id=callback_query.message.message_id)

                await bot.send_message(
                    chat_id=tg_id,
                    text=response_message,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )

        finally:
            await conn.close()

    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при получении ключей: {e}")

    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith('view_key|'))
async def process_callback_view_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    key_name, client_id = callback_query.data.split('|')[1], callback_query.data.split('|')[2]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow('''
                SELECT k.key, k.expiry_time, k.server_id 
                FROM keys k
                WHERE k.tg_id = $1 AND k.email = $2
            ''', tg_id, key_name)

            if record:
                key = record['key']
                expiry_time = record['expiry_time']
                server_id = record['server_id']

                server_name = SERVERS.get(server_id, {}).get('name', 'Неизвестный сервер')

                expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
                current_date = datetime.utcnow()
                time_left = expiry_date - current_date

                if time_left.total_seconds() <= 0:
                    days_left_message = "<b>Ключ истек.</b>"
                elif time_left.days > 0:
                    days_left_message = f"Осталось дней: <b>{time_left.days}</b>"
                else:
                    hours_left = time_left.seconds // 3600
                    days_left_message = f"Осталось часов: <b>{hours_left}</b>"

                formatted_expiry_date = expiry_date.strftime('%d %B %Y года')

                response_message = (
                    key_message(key, formatted_expiry_date, days_left_message, server_name)
                ) 

                renew_button = types.InlineKeyboardButton(text='⏳ Продлить ключ', callback_data=f'renew_key|{client_id}')
                instructions_button = types.InlineKeyboardButton(text='📘 Инструкции', callback_data='instructions')
                delete_button = types.InlineKeyboardButton(text='❌ Удалить ключ', callback_data=f'delete_key|{client_id}')
                change_location_button = types.InlineKeyboardButton(text='🌍 Сменить локацию', callback_data=f'change_location|{client_id}')
                back_button = types.InlineKeyboardButton(text='🔙 Назад в профиль', callback_data='view_profile')

                keyboard = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [instructions_button],  
                        [renew_button, delete_button], 
                        [change_location_button],  
                        [back_button] 
                    ]
                )

                await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard, parse_mode="HTML")
            else:
                await bot.edit_message_text("<b>Информация о ключе не найдена.</b>", chat_id=tg_id, message_id=callback_query.message.message_id, parse_mode="HTML")

        finally:
            await conn.close()

    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при получении информации о ключе: {e}")

    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith('delete_key|'))
async def process_callback_delete_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split('|')[1] 

    confirmation_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text='✅ Да, удалить', callback_data=f'confirm_delete|{client_id}')],
        [types.InlineKeyboardButton(text='❌ Нет, отменить', callback_data='view_keys')]
    ])

    await bot.edit_message_text("<b>Вы уверены, что хотите удалить ключ?</b>", chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=confirmation_keyboard, parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith('renew_key|'))
async def process_callback_renew_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split('|')[1] 

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow('SELECT email, expiry_time FROM keys WHERE client_id = $1', client_id)

            if record:
                email = record['email']
                expiry_time = record['expiry_time']
                current_time = datetime.utcnow().timestamp() * 1000  
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text=f'📅 1 месяц ({RENEWAL_PLANS["1"]["price"]} руб.)', callback_data=f'renew_plan|1|{client_id}')],
                    [types.InlineKeyboardButton(text=f'📅 3 месяца ({RENEWAL_PLANS["3"]["price"]} руб.)', callback_data=f'renew_plan|3|{client_id}')],
                    [types.InlineKeyboardButton(text=f'📅 6 месяцев ({RENEWAL_PLANS["6"]["price"]} руб.)', callback_data=f'renew_plan|6|{client_id}')],
                    [types.InlineKeyboardButton(text=f'📅 12 месяцев ({RENEWAL_PLANS["12"]["price"]} руб.)', callback_data=f'renew_plan|12|{client_id}')],
                    [types.InlineKeyboardButton(text='🔙 Назад', callback_data='view_profile')]
                ])

                balance = await get_balance(tg_id)
                response_message = PLAN_SELECTION_MSG.format(balance=balance, expiry_date=datetime.utcfromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S'))

                await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard, parse_mode="HTML")

        finally:
            await conn.close()

    except Exception as e:
        await bot.edit_message_text(f"<b>Ошибка при выборе плана:</b> {e}", chat_id=tg_id, message_id=callback_query.message.message_id, parse_mode="HTML")

    await callback_query.answer()


@router.callback_query(lambda c: c.data.startswith('confirm_delete|'))
async def process_callback_confirm_delete(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split('|')[1]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow('SELECT email, server_id FROM keys WHERE client_id = $1', client_id)

            if record:
                email = record['email']
                server_id = record['server_id']
                session = await login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
                success = await delete_client(session, server_id, client_id)

                if success:
                    await conn.execute('DELETE FROM keys WHERE client_id = $1', client_id)
                    response_message = "Ключ был успешно удален."
                else:
                    response_message = "Ошибка при удалении клиента через API."

            else:
                response_message = "Ключ не найден или уже удален."

            back_button = types.InlineKeyboardButton(text='Назад', callback_data='view_keys')
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

            await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard)

        finally:
            await conn.close()

    except Exception as e:
        await bot.edit_message_text(f"Ошибка при удалении ключа: {e}", chat_id=tg_id, message_id=callback_query.message.message_id)

    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith('renew_plan|'))
async def process_callback_renew_plan(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    plan, client_id = callback_query.data.split('|')[1], callback_query.data.split('|')[2]
    days_to_extend = 30 * int(plan)  

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow('SELECT email, expiry_time, server_id FROM keys WHERE client_id = $1', client_id)

            if record:
                email = record['email']
                expiry_time = record['expiry_time']
                server_id = record['server_id']  
                current_time = datetime.utcnow().timestamp() * 1000 

                if expiry_time <= current_time:
                    new_expiry_time = int(current_time + timedelta(days=days_to_extend).total_seconds() * 1000)
                else:
                    new_expiry_time = int(expiry_time + timedelta(days=days_to_extend).total_seconds() * 1000)

                cost = RENEWAL_PLANS[plan]['price']

                balance = await get_balance(tg_id)
                if balance < cost:
                    replenish_button = types.InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance')
                    back_button = types.InlineKeyboardButton(text='Назад', callback_data='view_profile')
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[replenish_button], [back_button]])

                    await bot.edit_message_text(INSUFFICIENT_FUNDS_MSG, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard)
                    return

                session = await login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
                success = await extend_client_key(session, server_id, tg_id, client_id, email, new_expiry_time)

                if success:
                    await update_balance(tg_id, -cost)
                    await conn.execute('UPDATE keys SET expiry_time = $1 WHERE client_id = $2', new_expiry_time, client_id)
                    response_message = SUCCESS_RENEWAL_MSG.format(months=RENEWAL_PLANS[plan]['months'])
                    back_button = types.InlineKeyboardButton(text='Назад', callback_data='view_profile')
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])
                    await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard)
                else:
                    await bot.edit_message_text(ERROR_RENEWAL_MSG, chat_id=tg_id, message_id=callback_query.message.message_id)
            else:
                await bot.edit_message_text(KEY_NOT_FOUND_MSG, chat_id=tg_id, message_id=callback_query.message.message_id)

        finally:
            await conn.close()

    except Exception as e:
        await bot.edit_message_text(f"Ошибка при продлении ключа: {e}", chat_id=tg_id, message_id=callback_query.message.message_id)

    await callback_query.answer()

async def handle_error(tg_id, callback_query, message):
    await bot.edit_message_text(message, chat_id=tg_id, message_id=callback_query.message.message_id)

@router.callback_query(lambda c: c.data.startswith('change_location|'))
async def process_callback_change_location(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split('|')[1]
    
    instructions_message = (
        "<b>Перед сменой локации:</b>\n\n"
        "1. Пожалуйста, отключите ваш VPN.\n"
        "2. Удалите старый ключ, чтобы избежать конфликтов.\n\n"
        "Теперь выберите новый сервер для вашего ключа:"
    )

    server_buttons = []
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for server_id, server in SERVERS.items():
            count = await conn.fetchval('SELECT COUNT(*) FROM keys WHERE server_id = $1', server_id)
            percent_full = (count / 60) * 100 if count <= 60 else 100  
            server_name = f"{server['name']} ({percent_full:.1f}%)"
            server_buttons.append([types.InlineKeyboardButton(text=server_name, callback_data=f'select_server&{server_id}&{client_id}')])
    finally:
        await conn.close()

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=server_buttons)
    
    await bot.edit_message_text(instructions_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard, parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith('select_server&'))
async def process_callback_select_server(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    server_id, client_id = callback_query.data.split('&')[1], callback_query.data.split('&')[2]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            async with conn.transaction():
                record = await conn.fetchrow(
                    'SELECT email, expiry_time, server_id FROM keys WHERE client_id = $1 FOR UPDATE', client_id
                )

                if record:
                    email = record['email']
                    expiry_time = record['expiry_time']
                    current_server_id = record['server_id']

                    if current_server_id == server_id:
                        await callback_query.answer("Клиент уже на этом сервере.")
                        return

                    session_new = await login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
                    new_client_data = await add_client(
                        session_new, server_id, client_id, email, tg_id, limit_ip=1, total_gb=0,
                        expiry_time=int(datetime.utcnow().timestamp() * 1000) + (expiry_time - datetime.utcnow().timestamp() * 1000),
                        enable=True, flow="xtls-rprx-vision"
                    )

                    if not new_client_data:
                        raise Exception("Ошибка при создании клиента на новом сервере.")

                    new_key = await link_subscription(email, server_id)

                    await conn.execute(
                        'UPDATE keys SET server_id = $1, key = $2 WHERE client_id = $3',
                        server_id, new_key, client_id
                    )

                    try:
                        session_old = await login_with_credentials(current_server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
                        success_delete = await delete_client(session_old, current_server_id, client_id)

                        if not success_delete:
                            raise Exception(f"Ошибка при удалении клиента с сервера {current_server_id}")

                        response_message = key_relocated(new_key)
                    except Exception as e:
                        response_message = f"Ключ перемещен, но возникла ошибка при удалении клиента с текущего сервера: {e}"

                else:
                    response_message = "Ключ не найден или уже удален."

            back_button = types.InlineKeyboardButton(text='Назад', callback_data='view_keys')
            iphone_button = types.InlineKeyboardButton(
                text='🍏IPhone',
                url=f'{APP_URL}/?url=v2raytun://import/{new_key}'
            )
            android_button = types.InlineKeyboardButton(
                text='🤖Android',
                url=f'{APP_URL}/?url=v2raytun://import-sub?url={new_key}'
            )

            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [back_button],
                    [iphone_button, android_button]
                ]
            )

            await bot.edit_message_text(
                response_message, chat_id=tg_id, message_id=callback_query.message.message_id,
                reply_markup=keyboard, parse_mode='HTML'
            )

        finally:
            await conn.close()

    except Exception as e:
        await bot.edit_message_text(
            f"Ошибка при смене локации: {e}", chat_id=tg_id, message_id=callback_query.message.message_id, parse_mode='HTML'
        )

    await callback_query.answer()
