# ═══════════════════════════════════════════════════════
# ДОБАВЬ ЭТО В telegram_bot.py
# Обработчик кнопки "OK ✓" из уведомления мини-аппа
# ═══════════════════════════════════════════════════════

@dp.callback_query(lambda c: c.data == "dismiss_notify")
async def dismiss_notification(callback: types.CallbackQuery):
    """Удаляет уведомление о готовой генерации по нажатию OK"""
    try:
        await callback.message.delete()
    except Exception:
        # если сообщение уже удалено — просто игнорируем
        pass
    await callback.answer()
