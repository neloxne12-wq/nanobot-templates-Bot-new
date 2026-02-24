# ═══════════════════════════════════════════════════════════════
# ДОБАВЬ ЭТО В telegram_bot.py
# Обработчик кнопки "OK ✓" из уведомления мини-аппа
# Вставь рядом с другими @dp.callback_query handlers
# ═══════════════════════════════════════════════════════════════

@dp.callback_query(lambda c: c.data == "dismiss_notify")
async def dismiss_notify(callback: types.CallbackQuery):
    """Удаляет уведомление о готовой генерации"""
    try:
        await callback.message.delete()
    except Exception:
        pass  # уже удалено — ок
    await callback.answer()
