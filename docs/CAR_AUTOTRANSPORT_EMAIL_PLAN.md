# План: переписка в карточках `Car` и `AutoTransport`

Цель: расширить систему email-переписки (сейчас работает для `Container`) на
карточки `Car` и `AutoTransport`, чтобы операторы могли видеть и вести всю
переписку по автомобилям и рейсам автовозов прямо из их карточек.

Базовый (уже работающий) функционал — см. `docs/CONTAINER_EMAIL_THREAD_PLAN.md`.
Там же описан M2M `ContainerEmail ↔ Container` через `ContainerEmailLink`,
per-link `is_read`, двусторонний sync UNREAD в Gmail, matched_by, thread-сцепка.

---

## Статус

| Фаза              | Состояние         | Коротко                                                      |
| ----------------- | ----------------- | ------------------------------------------------------------ |
| Phase 1 (Car)     | 🔜 К реализации   | VIN-матч, панель переписки в `CarAdmin`, строгий режим       |
| Phase 2 (AutoTrp) | 🔜 К реализации   | Агрегирующая вьюха, без отдельной M2M-таблицы                 |
| Phase 3 (ручное)  | 🔜 Отложено       | Ручная привязка писем к рейсу (переписка с водителем и т.п.) |

---

## Согласованные решения

| Вопрос                                                          | Ответ                                                     |
| --------------------------------------------------------------- | --------------------------------------------------------- |
| Доступ к карточкам `Car` / `AutoTransport`                      | Только сотрудники (staff). Клиенты не видят.              |
| Thread-матчинг для `Car`                                        | **Выключен.** Только явный VIN.                           |
| Авто-линк `Car → Container` при VIN-матче                       | **Нет.** Работает как сейчас: только если номер контейнера также упомянут (`MATCHED_BY_CONTAINER_NUMBER`). |
| Номер рейса `AutoTransport.number` в письмах                    | Не фигурирует (внутренний) — **не используем в матчере**. |
| `AutoTransport`: отдельная таблица связей                       | **Нет** на этой итерации. Агрегирующая вьюха через `CarEmailLink`. |
| Ручная привязка писем к `AutoTransport` (водитель/диспетчер)    | Отложено до Phase 3.                                       |
| Переименовывать `ContainerEmail → Email`                        | **Нет** — расширяем существующую модель новыми M2M.        |

---

## Почему именно так (ключевые обоснования)

**Риск «утечки» между клиентами в мультиклиентском автовозе.**
В одном рейсе могут быть машины разных клиентов. Если включить thread-матчинг
для `Car`, то переписка по VIN клиента A может всплыть в карточке машины
клиента B (через общий тред). Решение: **строгий режим** — в карточке `Car`
показываем ТОЛЬКО письма с явным её VIN. Ответы брокера вида *«принято»* без
VIN не привязываются ни к одной машине, но видны в карточке автовоза и в
admin-списке писем.

**Почему у `AutoTransport` нет своей таблицы.** Номер рейса не упоминается в
письмах, автоматом привязать нечего. Агрегирующая вьюха `emails_for_panel()`
через `cars` даёт оператору полную картину по рейсу без новой сущности.
`is_read` считается из `CarEmailLink`-ов машин рейса.

**Почему `ContainerEmail`, а не общий `Email`.** Миграция имён по всему
проекту (matcher, ingest, compose, admin, templates, views, tests) — высокий
риск что-то упустить. Имя модели косметическое, можно поменять потом через
`class Meta: db_table = ...` без данных.

---

## Архитектура моделей

### Новая таблица `CarEmailLink`

Полный аналог `ContainerEmailLink`:

```python
# core/models_email.py

class CarEmailLink(models.Model):
    email = models.ForeignKey(
        'ContainerEmail', on_delete=models.CASCADE, related_name='car_links',
    )
    car = models.ForeignKey(
        'Car', on_delete=models.CASCADE, related_name='email_links',
    )
    matched_by = models.CharField(
        max_length=20,
        choices=ContainerEmail.MATCHED_BY_CHOICES,
        default=ContainerEmail.MATCHED_BY_UNMATCHED,
        verbose_name='Как сопоставлено',
    )
    is_read = models.BooleanField(
        default=False,
        verbose_name='Прочитано в этой карточке авто',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Связь письма с машиной'
        verbose_name_plural = 'Связи писем с машинами'
        constraints = [
            models.UniqueConstraint(
                fields=['email', 'car'], name='unique_car_email_link',
            ),
        ]
        indexes = [
            models.Index(fields=['car', 'is_read']),
        ]
```

### Изменения в `ContainerEmail`

```python
cars = models.ManyToManyField(
    'Car',
    through='CarEmailLink',
    through_fields=('email', 'car'),
    related_name='emails',
    blank=True,
    verbose_name='Машины (по VIN)',
)
```

### Новый `matched_by` choice

```python
MATCHED_BY_VIN = 'vin'

MATCHED_BY_CHOICES = [
    # ... существующие ...
    (MATCHED_BY_VIN, 'VIN машины'),
]
```

### Методы на моделях (в `core/models.py`)

```python
class Car(models.Model):
    # ... существующие поля ...

    def emails_for_panel(self):
        """Письма для панели «Переписка» карточки машины.
        Строгий режим: только явный VIN-матч, без thread.
        """
        from django.db.models import OuterRef, Subquery
        from core.models_email import ContainerEmail, CarEmailLink
        return (
            ContainerEmail.objects
            .filter(cars__id=self.pk)
            .annotate(
                is_read_here=Subquery(
                    CarEmailLink.objects
                    .filter(email=OuterRef('pk'), car_id=self.pk)
                    .values('is_read')[:1]
                )
            )
            .distinct()
            .order_by('-received_at')
        )


class AutoTransport(models.Model):
    # ... существующие поля ...

    def emails_for_panel(self):
        """Агрегирующая вьюха: все письма, привязанные к машинам рейса.
        `is_read_here` = True только если ВСЕ CarEmailLink этого письма для
        машин рейса прочитаны. Иначе False (есть непрочитанные).
        """
        from django.db.models import OuterRef, Subquery, Exists
        from core.models_email import ContainerEmail, CarEmailLink
        car_ids = list(self.cars.values_list('id', flat=True))
        if not car_ids:
            return ContainerEmail.objects.none()
        has_unread = Exists(
            CarEmailLink.objects.filter(
                email=OuterRef('pk'),
                car_id__in=car_ids,
                is_read=False,
            )
        )
        return (
            ContainerEmail.objects
            .filter(cars__id__in=car_ids)
            .annotate(is_read_here=~has_unread)
            .distinct()
            .order_by('-received_at')
        )
```

---

## Миграция `0158_car_email_link`

Структура (без RunPython — чисто схема):

```python
# core/migrations/0158_car_email_link.py
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('core', '0157_containeremaillink_is_read'),  # последняя миграция
    ]
    operations = [
        # 1. AddField: ContainerEmail.matched_by choices — добавить 'vin'
        #    (на уровне Python; БД choices не хранит, но миграция нужна для
        #    history target). Если предыдущая миграция choices не фиксировала,
        #    эту операцию можно пропустить.
        migrations.AlterField(
            model_name='containeremail',
            name='matched_by',
            field=models.CharField(
                choices=[
                    ('unmatched', 'Не сопоставлено'),
                    ('container_number', 'По номеру контейнера'),
                    ('booking_number', 'По номеру букинга'),
                    ('thread', 'По треду'),
                    ('manual', 'Вручную'),
                    ('vin', 'VIN машины'),  # новое
                ],
                default='unmatched',
                max_length=20,
                verbose_name='Как сопоставлено',
            ),
        ),

        # 2. CreateModel: CarEmailLink
        migrations.CreateModel(
            name='CarEmailLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('matched_by', models.CharField(
                    max_length=20, default='unmatched',
                    choices=[...]  # те же choices
                )),
                ('is_read', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('car', models.ForeignKey(on_delete=models.CASCADE, related_name='email_links', to='core.car')),
                ('email', models.ForeignKey(on_delete=models.CASCADE, related_name='car_links', to='core.containeremail')),
            ],
            options={'verbose_name': 'Связь письма с машиной'},
        ),

        # 3. Constraints + indexes
        migrations.AddConstraint(
            model_name='caremaillink',
            constraint=models.UniqueConstraint(
                fields=('email', 'car'), name='unique_car_email_link',
            ),
        ),
        migrations.AddIndex(
            model_name='caremaillink',
            index=models.Index(fields=['car', 'is_read'], name='core_caremai_car_id_idx'),
        ),

        # 4. AddField: ContainerEmail.cars (M2M через CarEmailLink)
        migrations.AddField(
            model_name='containeremail',
            name='cars',
            field=models.ManyToManyField(
                through='core.CarEmailLink',
                to='core.car',
                related_name='emails',
                blank=True,
            ),
        ),
    ]
```

**Без backfill!** Пользователь явно сказал: уже полученные письма заново не
парсим, только новые. Это согласуется с тем, как сделано для M2M-контейнеров
(миграция `0156`).

---

## Matcher — `core/services/email_matcher.py`

### Новая регулярка VIN

```python
# VIN: 17 символов, только A-HJ-NPR-Z0-9 (без I, O, Q чтобы не путать с 1/0).
# Границы \b, чтобы не цеплять подстроки длинных строк.
_VIN_RE = re.compile(r'\b([A-HJ-NPR-Z0-9]{17})\b')
```

### Новый dataclass для хитов машин

```python
@dataclass(frozen=True)
class CarMatchHit:
    car_id: int
    matched_by: str = 'vin'  # единственный источник для Car

@dataclass
class MatchResult:
    hits: list[MatchHit] = field(default_factory=list)     # контейнеры (как сейчас)
    car_hits: list[CarMatchHit] = field(default_factory=list)  # НОВОЕ

    # ... остальное как сейчас ...
```

### Функция `_match_by_vins`

```python
def _match_by_vins(text: str) -> list[int]:
    """Возвращает id ВСЕХ машин, VIN которых упомянут в тексте.

    Сохраняем порядок появления в тексте (для primary-hit).
    """
    from core.models import Car

    if not text:
        return []
    upper = text.upper()
    candidates = set(_VIN_RE.findall(upper))
    if not candidates:
        return []

    order_map: dict[str, int] = {}
    for idx, m in enumerate(_VIN_RE.finditer(upper)):
        order_map.setdefault(m.group(1), idx)

    rows = list(
        Car.objects.filter(vin__in=candidates).values_list('id', 'vin')
    )
    rows.sort(key=lambda row: order_map.get((row[1] or '').upper(), 1_000_000))
    return [cid for cid, _vin in rows]
```

### Доработка `match_email_to_containers`

Добавить в конец функции (после container/booking matching) сбор car_hits:

```python
# 5) По VIN — для CarEmailLink (без thread-наследования)
from core.models_email import ContainerEmail
for car_id in _match_by_vins(haystack):
    result.car_hits.append(CarMatchHit(
        car_id=car_id,
        matched_by=ContainerEmail.MATCHED_BY_VIN,
    ))
```

**Важно:** VIN НЕ попадает в `hits` (контейнеры). Линковка машины к контейнеру
не делается автоматически — только если в том же письме также найден номер
контейнера через существующий `_match_by_container_numbers`.

---

## Ingest — `core/services/email_ingest.py`

В `_ingest_one`, после создания `ContainerEmailLink`-ов для match.hits,
добавить такой же блок для `CarEmailLink`:

```python
# ... после ContainerEmailLink.objects.bulk_create(...) ...

if match.car_hits:
    # При создании: если в Gmail у письма НЕТ UNREAD — сразу is_read=True
    # (reverse-sync). Только для INCOMING — чтобы не сбивать unread-бейджи
    # cross-linked карточек у OUTGOING.
    link_is_read = is_incoming and not gmail_is_unread
    car_links = [
        CarEmailLink(
            email=obj,
            car_id=hit.car_id,
            matched_by=hit.matched_by,
            is_read=link_is_read,
        )
        for hit in match.car_hits
    ]
    CarEmailLink.objects.bulk_create(car_links, ignore_conflicts=True)
```

Также в блоке `else:` (когда письмо уже существует) добавить проброс
`is_read=True` и на `CarEmailLink` при reverse-sync:

```python
if labels_changed and is_incoming and not gmail_is_unread:
    ContainerEmailLink.objects.filter(
        email_id=obj.pk, is_read=False,
    ).update(is_read=True)
    CarEmailLink.objects.filter(  # НОВОЕ
        email_id=obj.pk, is_read=False,
    ).update(is_read=True)
```

Не забыть импорт:
```python
from core.models_email import ContainerEmail, ContainerEmailLink, CarEmailLink
```

---

## Compose — `core/services/email_compose.py`

Две задачи:

**1) Отправка из карточки `Car`.**
Новая точка входа + origin_car:

```python
def compose_new_email_from_car(
    *, car, user, to, cc, bcc, subject, body_text, attachments=None,
) -> ContainerEmail:
    """Отправить новое письмо «по машине». Линкует ContainerEmail к Car
    (и к её контейнеру, если номер упомянут в subject/body через обычный
    matcher).
    """
    # ... аналогично compose_new_email, но:
    #   - sent_from_container = car.container (если есть)
    #   - после создания ContainerEmail, создаём CarEmailLink(car=car, is_read=True, matched_by=MANUAL)
    #   - кросс-линки по VIN/container#/booking# в тексте — как сейчас
```

Можно сделать общий helper `_compose_email(origin_container=None, origin_car=None, ...)`
и переиспользовать.

**2) Линковка outgoing к машинам.**
В функции `_link_outgoing_to_containers` (или её новой версии
`_link_outgoing`) после контейнеров добавить блок:

```python
if source_text:
    for car_id in _match_by_vins(source_text):
        CarEmailLink.objects.get_or_create(
            email=email, car_id=car_id,
            defaults={'matched_by': ContainerEmail.MATCHED_BY_VIN, 'is_read': False},
        )

# origin_car (если отправляли из карточки машины) → is_read=True
if origin_car is not None and origin_car.pk:
    CarEmailLink.objects.update_or_create(
        email=email, car=origin_car,
        defaults={'matched_by': ContainerEmail.MATCHED_BY_MANUAL, 'is_read': True},
    )
```

Не забыть импорт `_match_by_vins` из matcher.

---

## Views — `core/views/emails.py`

### Обобщить `email_mark_read`

Сейчас принимает `container_id`. Сделать универсальным через параметр `scope`:

```python
@staff_member_required
@require_POST
def email_mark_read(request, email_id: int):
    email = get_object_or_404(ContainerEmail, pk=email_id)
    new_val = request.POST.get('is_read', '1') == '1'
    scope = request.POST.get('scope', '')
    scope_id = request.POST.get('scope_id')

    if scope == 'car' and scope_id:
        qs = CarEmailLink.objects.filter(email_id=email.pk, car_id=scope_id)
    elif scope == 'autotransport' and scope_id:
        # Через машины рейса
        car_ids = list(AutoTransport.objects.get(pk=scope_id).cars.values_list('id', flat=True))
        qs = CarEmailLink.objects.filter(email_id=email.pk, car_id__in=car_ids)
    else:
        # default: контейнер (back-compat) или все links
        container_id = request.POST.get('container_id') or scope_id if scope == 'container' else None
        qs = ContainerEmailLink.objects.filter(email_id=email.pk)
        if container_id:
            qs = qs.filter(container_id=container_id)

    updated = qs.update(is_read=new_val)

    # Gmail reverse-sync
    if new_val and updated and email.direction == ContainerEmail.DIRECTION_INCOMING and email.gmail_id:
        _enqueue_gmail_mark_read([email.gmail_id])

    return JsonResponse({'ok': True, 'is_read': new_val, 'updated': updated})
```

Старые фронты с `container_id` — продолжат работать, т.к. мы оставляем его
обработку в ветке `else`.

### Новые эндпоинты

- `email_mark_car_read(car_id)` — пометить всю переписку машины прочитанной
  (аналог `email_mark_container_read`). Триггерит Gmail-sync.
- `email_mark_autotransport_read(at_id)` — то же для рейса: обновляем все
  CarEmailLink машин этого рейса.
- `email_car_updates(car_id)` — polling для panel на карточке машины.
- `email_autotransport_updates(at_id)` — polling для рейса.

Скопировать поведение с `email_mark_container_read` и `email_container_updates`,
заменив таблицы и Subquery.

### URLs (`core/urls.py`)

```python
path('emails/mark-car-read/<int:car_id>/', v.email_mark_car_read, name='email_mark_car_read'),
path('emails/mark-at-read/<int:at_id>/', v.email_mark_autotransport_read, name='email_mark_at_read'),
path('emails/car/<int:car_id>/updates/', v.email_car_updates, name='email_car_updates'),
path('emails/autotransport/<int:at_id>/updates/', v.email_autotransport_updates, name='email_at_updates'),
```

Плюс (если делаем отправку из карточки Car):

```python
path('emails/compose-from-car/', v.email_compose_send_from_car, name='email_compose_send_from_car'),
```

---

## Admin — блок «Переписка» в карточках

### `core/admin/car.py`

В `CarAdmin`:

```python
change_form_template = 'admin/core/car/change_form.html'
```

Создать `templates/admin/core/car/change_form.html` с инклюдом панели:

```html
{% extends "admin/change_form.html" %}
{% load static %}

{% block after_field_sets %}
{{ block.super }}
{% include "admin/core/car/_emails_panel.html" %}
{% endblock %}
```

### `core/admin/partners.py` → `AutoTransportAdmin`

То же самое:

```python
change_form_template = 'admin/core/autotransport/change_form.html'
```

---

## Templates — панели переписки

### Стратегия: переиспользуемый `_emails_panel.html`

Нынешний `templates/admin/core/container/_emails_panel.html` (1879 строк!) —
огромный. Рефакторить в 100% универсальный — больно. Прагматично:

1. Скопировать в `templates/admin/core/car/_emails_panel.html` и
   `templates/admin/core/autotransport/_emails_panel.html`.
2. В каждой копии поправить:
   - CSS-селекторы `#container-emails-section` → `#car-emails-section` / `#at-emails-section`
   - URL-ы endpoint'ов: `email_mark_container_read` → `email_mark_car_read`, и т.д.
   - `data-scope="container|car|autotransport"` + `data-scope-id` для fetch-запросов
   - Для `AutoTransport` — плашки над баббами: `VIN X · Клиент Y · Контейнер Z`
3. После стабилизации — вынести общие куски (CSS/JS) в отдельные partials
   `templates/admin/core/_emails_shared/`.

### `_email_bubble.html`

Уже использует `email.is_read_here` (из annotate). Для Car/AT работает без
изменений, т.к. в обоих `emails_for_panel` мы аннотируем это поле.

Для карточки `AutoTransport` добавить в bubble условный блок: если
`email.cars` содержит больше одной машины этого рейса — показать список VIN:

```html
{% if show_car_tags %}
  {% with car_set=email.cars.all %}
    <div class="cm-car-tags">
      {% for c in car_set %}
        {% if c.id in autotransport_car_ids %}
          <a href="/admin/core/car/{{ c.pk }}/change/">VIN {{ c.vin }}</a>
        {% endif %}
      {% endfor %}
    </div>
  {% endwith %}
{% endif %}
```

---

## Frontend — JS в панелях

Панели используют fetch с `container_id`. Надо передавать `scope` +
`scope_id`. Пример:

```js
// Было:
fetch(`/core/emails/mark-read/${emailId}/`, {
  method: 'POST',
  body: new URLSearchParams({ is_read: '1', container_id: '123' }),
});

// Стало (в car panel):
fetch(`/core/emails/mark-read/${emailId}/`, {
  method: 'POST',
  body: new URLSearchParams({ is_read: '1', scope: 'car', scope_id: '123' }),
});
```

В Container panel можно оставить `container_id` (back-compat).

---

## Админка — `CarEmailLinkAdmin`

По аналогии с `ContainerEmailLinkAdmin` в `core/admin/email.py`:

```python
@admin.register(CarEmailLink)
class CarEmailLinkAdmin(admin.ModelAdmin):
    list_display = ('email', 'car', 'matched_by', 'is_read', 'created_at')
    list_filter = ('matched_by', 'is_read')
    search_fields = ('email__subject', 'car__vin')
    raw_id_fields = ('email', 'car')
    readonly_fields = ('created_at',)
```

И `CarEmailLinkInline(admin.TabularInline)` — добавить в `ContainerEmailAdmin.inlines`
рядом с существующим `ContainerEmailLinkInline`.

---

## Чеклист реализации (пошагово)

### Шаг 1: Модели и миграция
- [ ] `core/models_email.py`: добавить `MATCHED_BY_VIN` в choices
- [ ] `core/models_email.py`: добавить класс `CarEmailLink`
- [ ] `core/models_email.py`: добавить `cars` M2M в `ContainerEmail`
- [ ] `core/models.py`: добавить `Car.emails_for_panel()`
- [ ] `core/models.py`: добавить `AutoTransport.emails_for_panel()`
- [ ] `python manage.py makemigrations core` → проверить `0158_*`
- [ ] `python manage.py migrate --plan` → проверить, что план разумный
- [ ] `python manage.py check`

### Шаг 2: Matcher
- [ ] `core/services/email_matcher.py`: `_VIN_RE`, `CarMatchHit`
- [ ] Расширить `MatchResult.car_hits`
- [ ] `_match_by_vins(text)` функция
- [ ] Интеграция в `match_email_to_containers` (отдельный блок после контейнеров)
- [ ] Unit test на матчер с VIN (если есть тесты matcher'а)

### Шаг 3: Ingest
- [ ] `core/services/email_ingest.py`: импорт `CarEmailLink`
- [ ] Создание `CarEmailLink` при `created=True`
- [ ] Reverse-sync `is_read=True` на CarEmailLink при `labels_changed`
- [ ] `manage.py check`

### Шаг 4: Compose
- [ ] `core/services/email_compose.py`: `origin_car` параметр + linking
- [ ] Кросс-линк по VIN в исходящих
- [ ] Новая точка входа `compose_new_email_from_car` (или общий helper)

### Шаг 5: Views + URLs
- [ ] Обобщить `email_mark_read` через `scope`
- [ ] `email_mark_car_read(car_id)`, `email_car_updates(car_id)`
- [ ] `email_mark_autotransport_read(at_id)`, `email_autotransport_updates(at_id)`
- [ ] Эндпоинт отправки из карточки Car (если делаем в этой итерации)
- [ ] URLs в `core/urls.py`

### Шаг 6: Admin
- [ ] `CarEmailLinkAdmin` в `core/admin/email.py`
- [ ] `CarEmailLinkInline` там же, подключить в `ContainerEmailAdmin.inlines`
- [ ] `CarAdmin.change_form_template`
- [ ] `AutoTransportAdmin.change_form_template`
- [ ] Обновить `ContainerEmailAdmin.read_status` — учитывать и CarEmailLink (или оставить только контейнеры)

### Шаг 7: Templates
- [ ] `templates/admin/core/car/change_form.html`
- [ ] `templates/admin/core/car/_emails_panel.html` (скопировать + адаптировать)
- [ ] `templates/admin/core/autotransport/change_form.html`
- [ ] `templates/admin/core/autotransport/_emails_panel.html`
- [ ] Плашки VIN/клиент в bubble для AT

### Шаг 8: Проверка локально
- [ ] `python manage.py check`
- [ ] `python manage.py makemigrations --dry-run` (должно быть clean)
- [ ] `python manage.py migrate` локально
- [ ] Открыть карточку Car — панель появляется, пустая
- [ ] Руками создать `CarEmailLink` в shell — баббл появился
- [ ] Открыть карточку AutoTransport с машинами — агрегация работает

### Шаг 9: Деплой
- [ ] `git add` + коммит (один или несколько логических)
- [ ] `git push`
- [ ] `.\scripts\deploy.ps1` (миграция прогоняется автоматически)
- [ ] Проверить логи celery — `sync_emails_from_gmail` отрабатывает без ошибок
- [ ] Проверить на живой карточке Car (с VIN, по которому есть письма)

---

## Рекомендуемая последовательность коммитов

1. `feat(email): M2M связь писем с машинами по VIN (модель + миграция)`
   — шаги 1, 6 (только admin для CarEmailLink)
2. `feat(email): VIN-матчинг в email_matcher + ingest`
   — шаги 2, 3
3. `feat(email): панель «Переписка» в карточке Car`
   — шаги 5 (car endpoints), 6 (CarAdmin), 7 (car templates)
4. `feat(email): агрегирующая панель «Переписка» в AutoTransport`
   — шаги 5 (AT endpoints), 6 (AutoTransportAdmin), 7 (AT templates)
5. `feat(email): отправка писем из карточки Car` *(опционально)*
   — шаг 4 + отдельный эндпоинт

Каждый коммит самодостаточен и может быть задеплоен независимо.

---

## Потенциальные грабли

1. **Конфликт unique-констрейнта `Car.vin`.** VIN уникален — проблем нет.
2. **Несколько VIN одного клиента в одном письме.** Нормально — создаются
   несколько `CarEmailLink`, каждый со своим `is_read`. Пользователь
   пометит прочитанным в одной карточке — остальные останутся непрочитанными
   (ожидаемое поведение).
3. **Re-matching кнопка.** В существующем `ContainerEmailAdmin.action_rematch`
   — добавить пересчёт и car_hits для выделенных писем. Полезно если матчер
   улучшили после ingest.
4. **Gmail-sync UNREAD при ручной разметке в Car card.** Уже работает через
   общий `_enqueue_gmail_mark_read` — главное чтобы `email_mark_read` при
   `scope=car` тоже его дёргал (см. код выше).
5. **AutoTransport.emails_for_panel() — производительность.** Если у рейса
   много машин (50+), запрос `filter(cars__id__in=car_ids).distinct()` может
   быть тяжёлым. Добавить `select_related('sent_from_container')` и
   `prefetch_related('containers', 'cars')` при необходимости.
6. **Старый фронт в Container panel использует `container_id`.** Оставить
   back-compat в `email_mark_read` — не ломаем существующий функционал.

---

## Чего НЕ делаем в этой итерации

- Ручная привязка писем к `AutoTransport` (для писем с водителем без VIN).
- Авто-линк «от email клиента» (`Client.email` → все его машины).
- UI для массового перепривязывания `CarEmailLink` из админки.
- Переименование `ContainerEmail → Email`.
- Thread-матчинг для Car (явно отключён по решению пользователя).
- AutoTransport.number в regex (внутренний номер, не фигурирует в письмах).

Эти пункты — кандидаты на Phase 3+.

---

## Ссылки на существующие референсы

| Что хочется сделать                            | Где смотреть образец                                         |
| ---------------------------------------------- | ------------------------------------------------------------ |
| Модель through со `matched_by` + `is_read`     | `core/models_email.py` → `ContainerEmailLink`                |
| Миграция с создания through + M2M              | `core/migrations/0156_containeremail_m2m_containers.py`      |
| Миграция с добавлением `is_read` к through     | `core/migrations/0157_containeremaillink_is_read.py`         |
| Regex матчинг + helper `_match_by_*`           | `core/services/email_matcher.py` → `_match_by_container_numbers` |
| Ingest: создание links при `created=True`      | `core/services/email_ingest.py` → `_ingest_one`               |
| Compose: linking outgoing + origin             | `core/services/email_compose.py` → `_link_outgoing_to_containers` |
| View `mark_read` с scope-парамом               | `core/views/emails.py` → `email_mark_read` (доработать)       |
| Polling updates endpoint                       | `core/views/emails.py` → `email_container_updates`            |
| Admin: change_form_template + inline emails    | `core/admin/container.py` → `ContainerAdmin`                  |
| `_email_bubble.html` + `is_read_here`          | `templates/admin/core/container/_email_bubble.html`          |
| Огромная панель с JS/CSS (для копирования)     | `templates/admin/core/container/_emails_panel.html`          |

---

## Gmail-sync (двусторонний) — уже работает

Документация: `docs/CONTAINER_EMAIL_THREAD_PLAN.md`. При реализации этого
плана важно:

- Во всех новых эндпоинтах `email_mark_*_read` вызывать
  `_enqueue_gmail_mark_read([email.gmail_id])` для INCOMING писем —
  чтобы sync в Gmail работал и из карточек Car/AutoTransport.
- В `email_ingest` reverse-sync уже предусмотрен для `ContainerEmailLink` —
  дописать его и для `CarEmailLink` (см. шаг 3).
