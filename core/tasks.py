import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_planned_notifications_task(self, container_id):
    from core.models import Container
    from core.services.email_service import ContainerNotificationService
    try:
        container = Container.objects.get(id=container_id)
        if not ContainerNotificationService.was_planned_notification_sent(container):
            sent, failed = ContainerNotificationService.send_planned_to_all_clients(container)
            logger.info(f"Planned notifications for {container.number}: {sent} sent, {failed} failed")
    except Exception as exc:
        logger.error(f"Failed planned notifications for container {container_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_unload_notifications_task(self, container_id):
    from core.models import Container
    from core.services.email_service import ContainerNotificationService
    try:
        container = Container.objects.get(id=container_id)
        if not ContainerNotificationService.was_unload_notification_sent(container):
            sent, failed = ContainerNotificationService.send_unload_to_all_clients(container)
            logger.info(f"Unload notifications for {container.number}: {sent} sent, {failed} failed")
    except Exception as exc:
        logger.error(f"Failed unload notifications for container {container_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_car_unload_notification_task(self, car_id):
    from core.models import Car
    from core.services.email_service import CarNotificationService
    try:
        car = Car.objects.select_related('client', 'warehouse').get(id=car_id)
        if not CarNotificationService.was_car_unload_notification_sent(car):
            CarNotificationService.send_car_unload_notification(car)
            logger.info(f"Car unload notification sent for {car.vin}")
    except Exception as exc:
        logger.error(f"Failed car unload notification for car {car_id}: {exc}")
        raise self.retry(exc=exc)
