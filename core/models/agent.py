"""Модели AI-агента (планировщик дел на Anthropic).

Архитектура (см. docs/AI_AGENT_PLAN.md):

* :class:`AgentRun` — один запуск агента (анализ письма, утренний план,
  исполнение дела). Хранит токены и стоимость для контроля бюджета.
* :class:`AgentAction` — единица намерения агента. ВСЁ, что агент хочет
  сделать с системой, проходит через AgentAction со статусной машиной
  PROPOSED → APPROVED/REJECTED → EXECUTED. Это одновременно журнал
  и approval-gate: «полная автономия» в будущем включается политикой
  (:class:`AgentPolicy`), а не переписыванием кода.
* :class:`AgentQuestion` — вопрос агента владельцу, когда он не знает,
  как действовать. Ответ дистиллируется в :class:`AgentMemory`.
* :class:`AgentMemory` — вечная память агента: правила, факты, контакты.
  Подтягивается в промпт по embedding-схожести (как RAG).
* :class:`AgentPolicy` — политика автономии: какие типы действий агент
  выполняет сам, а какие требуют подтверждения.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class AgentRun(models.Model):
    """Один запуск агента: анализ письма, планирование, исполнение дела."""

    KIND_EMAIL_ANALYSIS = "EMAIL_ANALYSIS"
    KIND_PLANNER = "PLANNER"
    KIND_EXECUTOR = "EXECUTOR"
    KIND_MEMORY_DISTILL = "MEMORY_DISTILL"
    KIND_CHOICES = [
        (KIND_EMAIL_ANALYSIS, "Анализ письма"),
        (KIND_PLANNER, "Планировщик"),
        (KIND_EXECUTOR, "Исполнение дела"),
        (KIND_MEMORY_DISTILL, "Дистилляция памяти"),
    ]

    STATUS_RUNNING = "RUNNING"
    STATUS_SUCCESS = "SUCCESS"
    STATUS_ERROR = "ERROR"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Выполняется"),
        (STATUS_SUCCESS, "Успешно"),
        (STATUS_ERROR, "Ошибка"),
    ]

    kind = models.CharField(max_length=20, choices=KIND_CHOICES, verbose_name="Тип запуска")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_RUNNING, verbose_name="Статус")
    started_at = models.DateTimeField(auto_now_add=True, verbose_name="Начало")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Конец")

    # Ссылка на вход (например "email:123" или "task:45") — для трассировки.
    input_ref = models.CharField(max_length=100, blank=True, default="", verbose_name="Вход")

    model = models.CharField(max_length=100, blank=True, default="", verbose_name="LLM-модель")
    input_tokens = models.IntegerField(default=0, verbose_name="Токены (вход)")
    output_tokens = models.IntegerField(default=0, verbose_name="Токены (выход)")
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0, verbose_name="Стоимость, $")

    result_json = models.JSONField(default=dict, blank=True, verbose_name="Результат")
    error = models.TextField(blank=True, default="", verbose_name="Ошибка")

    class Meta:
        verbose_name = "Запуск агента"
        verbose_name_plural = "Запуски агента"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["kind", "-started_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} #{self.pk} ({self.status})"

    def finish_ok(self, result: dict | None = None) -> None:
        self.status = self.STATUS_SUCCESS
        self.finished_at = timezone.now()
        if result is not None:
            self.result_json = result
        self.save(
            update_fields=[
                "status",
                "finished_at",
                "result_json",
                "input_tokens",
                "output_tokens",
                "cost_usd",
                "model",
            ]
        )

    def finish_error(self, error: str) -> None:
        self.status = self.STATUS_ERROR
        self.finished_at = timezone.now()
        self.error = (error or "")[:5000]
        self.save(
            update_fields=[
                "status",
                "finished_at",
                "error",
                "input_tokens",
                "output_tokens",
                "cost_usd",
                "model",
            ]
        )


class AgentAction(models.Model):
    """Намерение агента изменить что-то в системе.

    Статусная машина:

        PROPOSED → APPROVED → EXECUTED
                 → REJECTED              (владелец отклонил; причина — в память)
        PROPOSED → AUTO_EXECUTED         (политика разрешает авто-выполнение)
        APPROVED → FAILED                (ошибка при исполнении)
    """

    TYPE_CREATE_TASK = "CREATE_TASK"
    TYPE_DRAFT_EMAIL_REPLY = "DRAFT_EMAIL_REPLY"
    TYPE_COMPLETE_TASK = "COMPLETE_TASK"
    TYPE_UPDATE_OBJECT = "UPDATE_OBJECT"
    TYPE_SEND_EMAIL = "SEND_EMAIL"
    TYPE_OTHER = "OTHER"
    TYPE_CHOICES = [
        (TYPE_CREATE_TASK, "Создать дело"),
        (TYPE_DRAFT_EMAIL_REPLY, "Черновик ответа на письмо"),
        (TYPE_COMPLETE_TASK, "Закрыть дело"),
        (TYPE_UPDATE_OBJECT, "Изменить объект"),
        (TYPE_SEND_EMAIL, "Отправить письмо"),
        (TYPE_OTHER, "Другое"),
    ]

    RISK_READ = "READ"
    RISK_LOW = "LOW"
    RISK_MEDIUM = "MEDIUM"
    RISK_HIGH = "HIGH"
    RISK_CHOICES = [
        (RISK_READ, "Только чтение"),
        (RISK_LOW, "Низкий"),
        (RISK_MEDIUM, "Средний"),
        (RISK_HIGH, "Высокий (деньги/удаление)"),
    ]

    STATUS_PROPOSED = "PROPOSED"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_EXECUTED = "EXECUTED"
    STATUS_AUTO_EXECUTED = "AUTO_EXECUTED"
    STATUS_FAILED = "FAILED"
    STATUS_CHOICES = [
        (STATUS_PROPOSED, "Предложено"),
        (STATUS_APPROVED, "Одобрено"),
        (STATUS_REJECTED, "Отклонено"),
        (STATUS_EXECUTED, "Выполнено"),
        (STATUS_AUTO_EXECUTED, "Выполнено автоматически"),
        (STATUS_FAILED, "Ошибка исполнения"),
    ]

    run = models.ForeignKey(
        AgentRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actions",
        verbose_name="Запуск",
    )
    action_type = models.CharField(max_length=30, choices=TYPE_CHOICES, verbose_name="Тип действия")
    risk_level = models.CharField(max_length=10, choices=RISK_CHOICES, default=RISK_LOW, verbose_name="Уровень риска")
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default=STATUS_PROPOSED,
        db_index=True,
        verbose_name="Статус",
    )

    title = models.CharField(max_length=300, verbose_name="Краткое описание")
    # Параметры действия. Для CREATE_TASK: {title, description, priority,
    # deadline, car_id, container_id}. Для DRAFT_EMAIL_REPLY: {email_id,
    # reply_text}. Схема зависит от action_type.
    payload = models.JSONField(default=dict, blank=True, verbose_name="Параметры")
    reasoning = models.TextField(blank=True, default="", verbose_name="Обоснование агента")
    confidence = models.FloatField(null=True, blank=True, verbose_name="Уверенность (0-1)")

    source_email = models.ForeignKey(
        "ContainerEmail",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_actions",
        verbose_name="Письмо-источник",
    )
    task = models.ForeignKey(
        "Task",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_actions",
        verbose_name="Связанное дело",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="Решение принято")
    decided_by = models.CharField(max_length=100, blank=True, default="", verbose_name="Решил")
    reject_reason = models.TextField(blank=True, default="", verbose_name="Причина отклонения")
    executed_at = models.DateTimeField(null=True, blank=True, verbose_name="Выполнено в")
    result_json = models.JSONField(default=dict, blank=True, verbose_name="Результат исполнения")
    error = models.TextField(blank=True, default="", verbose_name="Ошибка")

    class Meta:
        verbose_name = "Действие агента"
        verbose_name_plural = "Действия агента"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["action_type", "status"]),
        ]

    def __str__(self) -> str:
        return f"[{self.get_action_type_display()}] {self.title[:60]} ({self.status})"

    def approve(self, by: str = "") -> None:
        self.status = self.STATUS_APPROVED
        self.decided_at = timezone.now()
        self.decided_by = by
        self.save(update_fields=["status", "decided_at", "decided_by"])

    def reject(self, by: str = "", reason: str = "") -> None:
        self.status = self.STATUS_REJECTED
        self.decided_at = timezone.now()
        self.decided_by = by
        self.reject_reason = reason
        self.save(update_fields=["status", "decided_at", "decided_by", "reject_reason"])

    def mark_executed(self, result: dict | None = None, *, auto: bool = False) -> None:
        self.status = self.STATUS_AUTO_EXECUTED if auto else self.STATUS_EXECUTED
        self.executed_at = timezone.now()
        if result is not None:
            self.result_json = result
        self.save(update_fields=["status", "executed_at", "result_json"])

    def mark_failed(self, error: str) -> None:
        self.status = self.STATUS_FAILED
        self.executed_at = timezone.now()
        self.error = (error or "")[:5000]
        self.save(update_fields=["status", "executed_at", "error"])


class AgentQuestion(models.Model):
    """Вопрос агента владельцу: «не знаю, как действовать — подскажи».

    Ответ владельца дистиллируется LLM'ом в правило AgentMemory, чтобы
    в следующий раз агент уже знал, что делать в подобной ситуации.
    """

    STATUS_OPEN = "OPEN"
    STATUS_ANSWERED = "ANSWERED"
    STATUS_DISMISSED = "DISMISSED"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Открыт"),
        (STATUS_ANSWERED, "Отвечен"),
        (STATUS_DISMISSED, "Закрыт без ответа"),
    ]

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
        db_index=True,
        verbose_name="Статус",
    )
    question = models.TextField(verbose_name="Вопрос агента")
    # Контекст ситуации: {email_id, subject, from_addr, intent, ...} — чтобы
    # владелец видел, о чём речь, а дистилляция имела исходные данные.
    context_json = models.JSONField(default=dict, blank=True, verbose_name="Контекст")

    run = models.ForeignKey(
        AgentRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questions",
        verbose_name="Запуск",
    )
    source_email = models.ForeignKey(
        "ContainerEmail",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_questions",
        verbose_name="Письмо-источник",
    )

    answer = models.TextField(blank=True, default="", verbose_name="Ответ владельца")
    answered_by = models.CharField(max_length=100, blank=True, default="", verbose_name="Ответил")
    answered_at = models.DateTimeField(null=True, blank=True, verbose_name="Отвечено в")
    memory = models.ForeignKey(
        "AgentMemory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="from_questions",
        verbose_name="Созданное правило",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")

    class Meta:
        verbose_name = "Вопрос агента"
        verbose_name_plural = "Вопросы агента"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Вопрос #{self.pk}: {self.question[:80]}"


class AgentMemory(models.Model):
    """Вечная память агента: правила, факты, контакты.

    Каждая запись — атомарное знание («Письма от X про страховку — пересылать
    брокеру и не создавать дело»). Embedding позволяет подтягивать релевантные
    записи в промпт при анализе нового письма (retrieval как в RAG).
    """

    KIND_RULE = "RULE"
    KIND_FACT = "FACT"
    KIND_CONTACT = "CONTACT"
    KIND_CHOICES = [
        (KIND_RULE, "Правило (как действовать)"),
        (KIND_FACT, "Факт о бизнесе"),
        (KIND_CONTACT, "Контакт / отправитель"),
    ]

    SOURCE_QUESTION = "QUESTION"
    SOURCE_MANUAL = "MANUAL"
    SOURCE_REJECTION = "REJECTION"
    SOURCE_CHOICES = [
        (SOURCE_QUESTION, "Из ответа на вопрос"),
        (SOURCE_MANUAL, "Добавлено вручную"),
        (SOURCE_REJECTION, "Из отклонения предложения"),
    ]

    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_RULE, verbose_name="Тип")
    content = models.TextField(
        verbose_name="Содержание",
        help_text="Само правило/факт в 1-3 предложениях. Попадает в промпт агента.",
    )
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_MANUAL, verbose_name="Источник")
    # Embedding содержимого (list[float]) для retrieval по схожести.
    # None — embedding недоступен (нет AI_API_KEY); fallback на keyword-поиск.
    embedding = models.JSONField(null=True, blank=True, verbose_name="Embedding")

    is_active = models.BooleanField(default=True, db_index=True, verbose_name="Активно")
    times_used = models.IntegerField(default=0, verbose_name="Использований")
    last_used_at = models.DateTimeField(null=True, blank=True, verbose_name="Последнее использование")

    created_by = models.CharField(max_length=100, blank=True, default="", verbose_name="Создал")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Память агента"
        verbose_name_plural = "Память агента"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"[{self.get_kind_display()}] {self.content[:80]}"


class AgentPolicy(models.Model):
    """Политика автономии: что агент делает сам, а что — с подтверждением.

    По одной записи на тип действия (:attr:`AgentAction.action_type`).
    Отсутствие записи = режим ASK (спрашивать). Перевод типа действия
    в AUTO — осознанное решение владельца, принимаемое по мере роста
    доверия к агенту. Так «полная свобода действий» включается данными,
    а не изменением кода.
    """

    MODE_ASK = "ASK"
    MODE_AUTO = "AUTO"
    MODE_DISABLED = "DISABLED"
    MODE_CHOICES = [
        (MODE_ASK, "Спрашивать подтверждение"),
        (MODE_AUTO, "Выполнять автоматически"),
        (MODE_DISABLED, "Запрещено"),
    ]

    action_type = models.CharField(
        max_length=30, choices=AgentAction.TYPE_CHOICES, unique=True, verbose_name="Тип действия"
    )
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=MODE_ASK, verbose_name="Режим")
    comment = models.TextField(blank=True, default="", verbose_name="Комментарий")
    updated_by = models.CharField(max_length=100, blank=True, default="", verbose_name="Изменил")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Политика автономии"
        verbose_name_plural = "Политики автономии"
        ordering = ["action_type"]

    def __str__(self) -> str:
        return f"{self.get_action_type_display()} → {self.get_mode_display()}"

    @classmethod
    def mode_for(cls, action_type: str) -> str:
        """Режим для типа действия. Нет записи — спрашивать (безопасный дефолт)."""
        policy = cls.objects.filter(action_type=action_type).only("mode").first()
        return policy.mode if policy else cls.MODE_ASK
