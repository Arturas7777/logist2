"""Утилиты для временного отключения Django-сигналов на время bulk-операций.

Типичный случай использования — массовое удаление транзакций + инвойсов в админ-
action'е, где каждый delete() иначе триггерил бы пересчёт баланса. С отключёнными
сигналами делаем один финальный пересчёт в конце, это O(1) вместо O(N).

Пример:

    from core.signal_utils import signals_muted
    from django.db.models.signals import post_save, post_delete
    from core.models_billing import Transaction

    with signals_muted(post_save, post_delete, senders=(Transaction,)):
        txs.delete()
    # финальный пересчёт балансов вручную
"""

from contextlib import contextmanager


@contextmanager
def signals_muted(*signals, senders=()):
    """Временно глушит приёмники переданных signals для senders.

    Реализация: полностью сохраняем/восстанавливаем `signal.receivers` —
    самый надёжный и простой способ, не опирающийся на weak refs или
    точные lookup_key. Глушим только в отсутствие других потоков с
    активными сигналами в этой точке — допустимо, т.к. используется
    во время admin-action/management command.
    """
    saved = []
    for sig in signals:
        saved.append((sig, list(sig.receivers)))
        if senders:
            # Оставляем приёмники, которые привязаны к другим sender'ам.
            # lookup_key у Django — (id(receiver), id(sender)).
            sender_ids = {id(s) for s in senders}
            sig.receivers = [
                (key, r) for (key, r) in sig.receivers
                if key[1] not in sender_ids
            ]
            # В Django также есть sender_receivers_cache — сбросим, чтобы не читал из кэша.
            try:
                sig.sender_receivers_cache.clear()
            except Exception:
                pass
        else:
            sig.receivers = []
            try:
                sig.sender_receivers_cache.clear()
            except Exception:
                pass
    try:
        yield
    finally:
        for sig, original in saved:
            sig.receivers = original
            try:
                sig.sender_receivers_cache.clear()
            except Exception:
                pass
