from typing import Dict, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.models import User
from . import texts, texts_en, buttons, buttons_en


# Локализованные словари месяцев
MONTHS_LOCALIZED = {
    "ru": {
        "January": "Января",
        "February": "Февраля",
        "March": "Марта",
        "April": "Апреля",
        "May": "Мая",
        "June": "Июня",
        "July": "Июля",
        "August": "Августа",
        "September": "Сентября",
        "October": "Октября",
        "November": "Ноября",
        "December": "Декабря",
    },
    "en": {
        "January": "January",
        "February": "February",
        "March": "March",
        "April": "April",
        "May": "May",
        "June": "June",
        "July": "July",
        "August": "August",
        "September": "September",
        "October": "October",
        "November": "November",
        "December": "December",
    }
}


class LanguageManager:
    """Менеджер для определения и управления языками пользователей"""
    
    DEFAULT_LANGUAGE = "ru"
    SUPPORTED_LANGUAGES = ["ru", "en"]
    
    @staticmethod
    async def get_user_language(session: AsyncSession, tg_id: int) -> str:
        """
        Определяет язык пользователя на основе его настроек в Telegram
        
        Args:
            session: Сессия базы данных
            tg_id: ID пользователя в Telegram
            
        Returns:
            str: Код языка ('ru' или 'en')
        """
        try:
            # Получаем данные пользователя из базы
            result = await session.execute(
                select(User.language_code).where(User.tg_id == tg_id)
            )
            user_language_code = result.scalar_one_or_none()
            
            if not user_language_code:
                return LanguageManager.DEFAULT_LANGUAGE
            
            # Если язык русский, используем русский
            if user_language_code.startswith('ru'):
                return "ru"
            
            # Для всех остальных языков используем английский
            return "en"
            
        except Exception:
            return LanguageManager.DEFAULT_LANGUAGE
    
    @staticmethod
    def get_user_language_from_telegram(language_code: Optional[str]) -> str:
        """
        Определяет язык на основе language_code из Telegram
        
        Args:
            language_code: Код языка из Telegram
            
        Returns:
            str: Код языка ('ru' или 'en')
        """
        if not language_code:
            return LanguageManager.DEFAULT_LANGUAGE
        
        # Если язык русский, используем русский
        if language_code.startswith('ru'):
            return "ru"
        
        # Для всех остальных языков используем английский
        return "en"

    @staticmethod
    def get_texts_module(language: str):
        """
        Возвращает модуль с текстами для указанного языка
        
        Args:
            language: Код языка
            
        Returns:
            Модуль с текстами
        """
        if language == "en":
            return texts_en
        return texts
    
    @staticmethod
    def get_buttons_module(language: str):
        """
        Возвращает модуль с кнопками для указанного языка
        
        Args:
            language: Код языка
            
        Returns:
            Модуль с кнопками
        """
        if language == "en":
            return buttons_en
        return buttons


def get_localized_text(text_dict: Dict[str, str], language: str = "ru") -> str:
    """
    Возвращает локализованный текст
    
    Args:
        text_dict: Словарь с переводами {lang_code: text}
        language: Код языка
        
    Returns:
        str: Локализованный текст
    """
    return text_dict.get(language, text_dict.get(LanguageManager.DEFAULT_LANGUAGE, ""))


def get_localized_button(button_name: str, language: str = "ru") -> str:
    """
    Возвращает локализованную кнопку
    
    Args:
        button_name: Название кнопки (константа)
        language: Код языка
        
    Returns:
        str: Локализованный текст кнопки
    """
    buttons_module = LanguageManager.get_buttons_module(language)
    return getattr(buttons_module, button_name, getattr(buttons, button_name, button_name))


async def get_user_texts(session: AsyncSession, tg_id: int):
    """
    Возвращает модуль с текстами для пользователя
    
    Args:
        session: Сессия базы данных
        tg_id: ID пользователя в Telegram
        
    Returns:
        Модуль с текстами
    """
    language = await LanguageManager.get_user_language(session, tg_id)
    return LanguageManager.get_texts_module(language)


async def get_user_buttons(session: AsyncSession, tg_id: int):
    """
    Возвращает модуль с кнопками для пользователя
    
    Args:
        session: Сессия базы данных
        tg_id: ID пользователя в Telegram
        
    Returns:
        Модуль с кнопками
    """
    language = await LanguageManager.get_user_language(session, tg_id)
    return LanguageManager.get_buttons_module(language)


def get_localized_month(date: datetime, language: str = "ru") -> str:
    """
    Преобразует английское название месяца в локализованное.

    Args:
        date: Объект datetime, из которого извлекается месяц.
        language: Код языка для локализации ('ru' или 'en').

    Returns:
        Название месяца на указанном языке.
    """
    english_month = date.strftime("%B")
    months_dict = MONTHS_LOCALIZED.get(language, MONTHS_LOCALIZED["ru"])
    return months_dict.get(english_month, english_month)


async def get_localized_month_for_user(session: AsyncSession, tg_id: int, date: datetime) -> str:
    """
    Возвращает локализованное название месяца для пользователя
    
    Args:
        session: Сессия базы данных
        tg_id: ID пользователя в Telegram
        date: Дата для извлечения месяца
        
    Returns:
        str: Локализованное название месяца
    """
    language = await LanguageManager.get_user_language(session, tg_id)
    return get_localized_month(date, language) 