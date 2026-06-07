# Классификация сигналов Logist2 (Фаза 3 рефакторинга)

Документ фиксирует разметку всех `@receiver`-обработчиков (и сигналов,
подключённых вручную) по двум категориям:

- **EVENT** — реакция-нотификация на факт изменения: инвалидация кэша,
  WebSocket, e-mail/Telegram, внешний push. Не меняет бизнес-данные
  приложения. Сигналы здесь — *правильное* применение паттерна, их
  трогать не нужно.
- **COMMAND** — обработчик меняет бизнес-данные (создаёт/удаляет/
  пересчитывает записи). Часть команд — это **реактивная
  денормализация** (пересчёт производных полей в ответ на изменение
  источника), которая для сигналов уместна. Часть — **оркестрация
  пользовательского действия**, которую логичнее вызывать явно из
  сервиса (цель PR 3.2/3.3).

Подкатегории COMMAND:

- `COMMAND/denorm` — пересчёт производных данных (баланс, total_price,
  paid_amount). Оставляем сигналом: реакция на событие, дешёвая или
  вынесена в Celery, нужна из любого места записи.
- `COMMAND/orchestration` — создание/перестроение сущностей по факту
  пользовательского действия. Кандидаты на явный вызов из оркестраторов
  фазы 1 (`car_admin_service`, `container_lifecycle_service`).

> Защитные флаги (`_bulk_updating`, `_creating_services`,
> `_skip_balance_recalc`, `_pushing_to_sitepro`, `_syncing_linked`) и
> thread-local дедупликация (`_pricing_local`, `_regen_local`) пока
> остаются: они предотвращают рекурсию и N+1. Их снятие — PR 3.4, только
> после того как соответствующие команды станут явными (см. «Отложено»).

---

## Таблица приёмников

| Модуль / receiver | Сигнал · модель | Класс | Что делает | Treatment |
|---|---|---|---|---|
| `car.save_old_car_values` | pre_save · Car | infra | Снимок старых полей для post_save | keep |
| `car.car_post_save` | post_save · Car | mixed | См. разбор ниже (7 задач) | split |
| `car_service.recalculate_car_price_on_service_save/_delete` | post/delete · CarService | COMMAND/denorm | Пересчёт `Car.total_price/days/storage_cost` (on_commit, дедуп) | keep |
| `car_service.recalculate_invoices_on_car_service_save/_delete` | post/delete · CarService | COMMAND/denorm | Регенерация позиций инвойсов (Celery, дедуп) | keep |
| `service_catalog.update_cars_on_*_service_change` (×4) | post_save · *Service | COMMAND/denorm | Массовое обновление `CarService` + пересчёт цены | keep |
| `service_catalog.delete_car_services_on_*_delete` (×4) | pre_delete · *Service | COMMAND/denorm | Каскадное удаление `CarService` (FK без PROTECT) | keep |
| `container.save_old_container_values` | pre_save · Container | infra | Авто-статус UNLOADED + снимок старых полей | keep |
| `container.update_related_on_container_save` | post_save · Container | COMMAND/denorm | Распространение `unload_date` на авто + пересчёт (Celery) | keep |
| `container.send_container_notifications_on_save` | post_save · Container | EVENT | E-mail/Telegram о plan/unload-датах (Celery) | keep |
| `container.auto_sync_photos_on_container_change` | post_save · Container | EVENT | Лог-маркер для cron-синка фото | keep |
| `transaction.recalculate_on_transaction_save/_delete` | post/delete · Transaction | COMMAND/denorm | Пересчёт балансов + `paid_amount` инвойса (синхронно) | keep |
| `bank.auto_create_payment_on_bt_match` | post_save · BankTransaction | COMMAND/orchestration | Создаёт `Transaction(PAYMENT)` при привязке | candidate |
| `bank._track_bt_matched_invoice_change` | pre_save · BankTransaction | infra | Снимок старого `matched_invoice` | keep |
| `invoice.auto_categorize_invoice` | pre_save · NewInvoice | COMMAND/denorm | Дефолтная категория OPERATIONAL | keep |
| `invoice.save_old_invoice_status` | pre_save · NewInvoice | infra | Снимок старого статуса | keep |
| `invoice.auto_push_invoice_to_sitepro` | post_save · NewInvoice | EVENT | Push в site.pro (Celery, внешняя система) | keep |
| `invoice.sync_linked_invoice_status` | post_save · NewInvoice | COMMAND/denorm | Парный инвойс → `LINKED_PAID` | keep |
| `autotransport.autotransport_pre_save` | pre_save · AutoTransport | infra | Снимок старого статуса | keep |
| `autotransport.autotransport_post_save` | post_save · AutoTransport | COMMAND/orchestration | Генерация инвойсов автовоза, массовый TRANSFERRED | candidate |
| `autotransport.autotransport_cars_changed_handler` | m2m_changed · cars | COMMAND/denorm | Валидация «Важное», пересчёт | keep |
| `photos.invalidate_gallery_on_photo_save/_delete` | post/delete · ContainerPhoto | EVENT | Инвалидация кэша галереи | keep |
| `cache_invalidation._invalidate_stats_cache` | post/delete · (модели) | EVENT | Инвалидация stats/payment_objects-кэша | keep |
| `service_cache.invalidate_service_cache` | post/delete · *Service | EVENT | Инвалидация per-instance кэша услуг | keep |

---

## Разбор `car_post_save` (главный смешанный обработчик)

Выполняет 7 задач по порядку:

| № | Задача | Класс |
|---|---|---|
| 1 | `_create_car_services_if_needed` — создание/перестроение `CarService` при смене контрактника | **COMMAND/orchestration** |
| 2 | `_deferred_invoice_regeneration` — регенерация инвойсов (Celery) | COMMAND/denorm |
| 3 | `_maybe_send_car_unload_notification` — e-mail/Telegram | **EVENT** |
| 4 | `_update_container_status_if_all_transferred` — авто-статус контейнера | COMMAND/denorm |
| 5 | `_handle_car_important_transition` — создание/закрытие `Task` по `is_important` | **COMMAND/orchestration** |
| 6 | `_enqueue_recalc_cars_total_price` — пересчёт цены (Celery) | COMMAND/denorm |
| 7 | `_enqueue_car_ws_notification` — WebSocket data_update | **EVENT** |

Задача 7 (WS) дублировала `car_lifecycle_service.send_car_ws_notification`.
В рамках Фазы 3 сигнал делегирует EVENT-нотификацию сервису (единый
источник), оставаясь тонкой обёрткой — поведение не меняется.

---

## Сделано в Фазе 3 (PR 3.1 + безопасный инкремент PR 3.2)

- Все приёмники классифицированы (эта таблица).
- WS-нотификация (`car_post_save`, задача 7) вынесена в единственную
  реализацию `car_lifecycle_service.send_car_ws_notification`; дубликат
  `_enqueue_car_ws_notification` удалён. EVENT-логика теперь живёт в
  сервисе, сигнал — тонкая обёртка.
- В `car_post_save` добавлены маркеры `EVENT`/`COMMAND` к каждому блоку.

## Отложено (требует периода наблюдения, как PR 2.4)

- **PR 3.2 (полностью) / 3.3** — перенос `COMMAND/orchestration`
  (`_create_car_services_if_needed`, `_handle_car_important_transition`,
  генерация инвойсов автовоза, авто-`Transaction` из банка) в явные
  вызовы сервисов с последующим снятием сигнал-обёрток. Риск средний:
  сигналы стреляют из множества точек записи (админка, импорт,
  management-команды, API), поэтому перенос делается по одному
  обработчику с прогоном сети безопасности и наблюдением на проде.
- **PR 3.4** — снятие защитных флагов и упрощение thread-local
  дедупликации. Возможно только после того, как соответствующие
  команды станут явными (иначе вернётся рекурсия / N+1).
