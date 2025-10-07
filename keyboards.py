from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_main_menu_keyboard():
    """Клавиатура главного меню"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("🔄 Статус системы", callback_data="status")],
        [InlineKeyboardButton("🤖 Авто-посты", callback_data="auto_posts")],
        [InlineKeyboardButton("📝 Создать пост", callback_data="create_post")],
        [InlineKeyboardButton("📅 Контент-план", callback_data="content_plan")],
        [InlineKeyboardButton("📋 Запланированные посты", callback_data="scheduled_posts")],
        [InlineKeyboardButton("🔒 Проверить права", callback_data="check_permissions")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ])

def get_tone_keyboard(prefix="tone"):
    """Клавиатура выбора тона"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("😊 Дружелюбный", callback_data=f"{prefix}_friendly")],
        [InlineKeyboardButton("🎭 Юмористический", callback_data=f"{prefix}_funny")],
        [InlineKeyboardButton("💼 Серьёзный", callback_data=f"{prefix}_serious")],
        [InlineKeyboardButton("🚀 Вдохновляющий", callback_data=f"{prefix}_inspirational")],
        [InlineKeyboardButton("👔 Профессиональный", callback_data=f"{prefix}_professional")]
    ])

def get_length_keyboard():
    """Клавиатура выбора длины поста"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("�� Короткий (50-100 символов)", callback_data="length_short")],
        [InlineKeyboardButton("📝 Средний (200-300 символов)", callback_data="length_medium")],
        [InlineKeyboardButton("📄 Длинный (400-600 символов)", callback_data="length_long")]
    ])

def get_content_plan_type_keyboard():
    """Клавиатура выбора типа контент-плана"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Недельный план", callback_data="content_plan_weekly")],
        [InlineKeyboardButton("🗓️ Месячный план", callback_data="content_plan_monthly")],
        [InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]
    ])
