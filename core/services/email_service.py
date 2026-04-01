"""
Сервис отправки email-уведомлений клиентам о контейнерах и отдельных ТС
"""
import json
import logging
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


class ContainerNotificationService:
    """
    Сервис для отправки уведомлений клиентам о контейнерах.
    Поддерживает отправку на несколько email-адресов одного клиента.
    """
    
    @staticmethod
    def send_planned_notification(container, client, user=None):
        """
        Отправляет уведомление о планируемой дате разгрузки на все email клиента.
        
        Args:
            container: объект Container
            client: объект Client
            user: пользователь, инициировавший отправку (опционально)
        
        Returns:
            bool: True если хотя бы одно письмо отправлено успешно
        """
        # Проверяем наличие email-адресов и включены ли уведомления
        if not client.has_notification_emails() or not client.notification_enabled:
            logger.warning(f"Cannot send planned notification to {client.name}: no emails or notifications disabled")
            return False
        
        if not container.planned_unload_date:
            logger.warning(f"Cannot send planned notification for {container.number}: no planned_unload_date set")
            return False
        
        # Получаем автомобили этого клиента в контейнере
        cars = container.container_cars.filter(client=client)
        if not cars.exists():
            logger.warning(f"No cars for client {client.name} in container {container.number}")
            return False
        
        cars_list = [{'vin': car.vin, 'brand': car.brand, 'year': car.year} for car in cars]
        
        _site_name, site_address = container.get_unload_address()
        
        context = {
            'container_number': container.number,
            'planned_date': container.planned_unload_date,
            'warehouse': container.warehouse.name if container.warehouse else 'Не указан',
            'warehouse_address': site_address,
            'cars': cars_list,
            'client_name': client.name,
            'company_name': getattr(settings, 'COMPANY_NAME', 'Caromoto Lithuania'),
            'company_phone': getattr(settings, 'COMPANY_PHONE', ''),
            'company_email': getattr(settings, 'COMPANY_EMAIL', ''),
            'company_website': getattr(settings, 'COMPANY_WEBSITE', ''),
        }
        
        subject = f"Планируемая разгрузка контейнера {container.number}"
        
        return ContainerNotificationService._send_notification_to_all_emails(
            notification_type='PLANNED',
            container=container,
            client=client,
            subject=subject,
            template_name='email/planned_notification.html',
            context=context,
            cars_list=cars_list,
            user=user
        )
    
    @staticmethod
    def send_unload_notification(container, client, user=None):
        """
        Отправляет уведомление о фактической разгрузке контейнера на все email клиента.
        
        Args:
            container: объект Container
            client: объект Client
            user: пользователь, инициировавший отправку (опционально)
        
        Returns:
            bool: True если хотя бы одно письмо отправлено успешно
        """
        # Проверяем наличие email-адресов и включены ли уведомления
        if not client.has_notification_emails() or not client.notification_enabled:
            logger.warning(f"Cannot send unload notification to {client.name}: no emails or notifications disabled")
            return False
        
        if not container.unload_date:
            logger.warning(f"Cannot send unload notification for {container.number}: no unload_date set")
            return False
        
        # Получаем автомобили этого клиента в контейнере
        cars = container.container_cars.filter(client=client)
        if not cars.exists():
            logger.warning(f"No cars for client {client.name} in container {container.number}")
            return False
        
        cars_list = [{'vin': car.vin, 'brand': car.brand, 'year': car.year} for car in cars]
        
        _site_name, site_address = container.get_unload_address()
        
        context = {
            'container_number': container.number,
            'unload_date': container.unload_date,
            'warehouse': container.warehouse.name if container.warehouse else 'Не указан',
            'warehouse_address': site_address,
            'cars': cars_list,
            'client_name': client.name,
            'company_name': getattr(settings, 'COMPANY_NAME', 'Caromoto Lithuania'),
            'company_phone': getattr(settings, 'COMPANY_PHONE', ''),
            'company_email': getattr(settings, 'COMPANY_EMAIL', ''),
            'company_website': getattr(settings, 'COMPANY_WEBSITE', ''),
        }
        
        subject = f"Контейнер {container.number} разгружен"
        
        return ContainerNotificationService._send_notification_to_all_emails(
            notification_type='UNLOADED',
            container=container,
            client=client,
            subject=subject,
            template_name='email/unload_notification.html',
            context=context,
            cars_list=cars_list,
            user=user
        )
    
    @staticmethod
    def _send_notification_to_all_emails(notification_type, container, client, subject, template_name, context, cars_list, user=None):
        """
        Отправляет уведомление на все email-адреса клиента.
        Каждый email получает отдельное письмо и логируется отдельно.
        
        Returns:
            bool: True если хотя бы одно письмо отправлено успешно
        """
        emails = client.get_notification_emails()
        
        if not emails:
            logger.warning(f"No emails found for client {client.name}")
            return False
        
        # Рендерим HTML шаблон один раз для всех получателей
        html_content = render_to_string(template_name, context)
        text_content = strip_tags(html_content)
        
        success_count = 0
        
        # Отправляем на каждый email отдельно
        for email_to in emails:
            success = ContainerNotificationService._send_single_email(
                notification_type=notification_type,
                container=container,
                client=client,
                email_to=email_to,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
                cars_list=cars_list,
                user=user
            )
            if success:
                success_count += 1
        
        logger.info(f"📧 Sent {success_count}/{len(emails)} emails for {notification_type} notification to {client.name}")
        
        # Возвращаем True если хотя бы одно письмо отправлено успешно
        return success_count > 0
    
    @staticmethod
    def _send_single_email(notification_type, container, client, email_to, subject, html_content, text_content, cars_list, user=None):
        """
        Отправляет одно письмо на указанный email и логирует результат.
        """
        from core.models_website import NotificationLog
        
        error_message = ''
        success = False
        
        try:
            # Создаём email
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[email_to]
            )
            email.attach_alternative(html_content, "text/html")
            
            # Отправляем
            email.send(fail_silently=False)
            
            success = True
            logger.info(f"✅ Email sent: {notification_type} for {container.number} to {email_to}")
            
        except Exception as e:
            error_message = str(e)
            logger.error(f"❌ Failed to send email: {notification_type} for {container.number} to {email_to}: {e}")
        
        # Логируем отправку
        try:
            NotificationLog.objects.create(
                container=container,
                client=client,
                notification_type=notification_type,
                email_to=email_to,
                subject=subject,
                cars_info=json.dumps(cars_list, ensure_ascii=False),
                success=success,
                error_message=error_message,
                created_by=user
            )
        except Exception as e:
            logger.error(f"Failed to create notification log: {e}")
        
        return success
    
    @staticmethod
    def send_planned_to_all_clients(container, user=None):
        """
        Отправляет уведомление о планируемой разгрузке всем клиентам с автомобилями в контейнере.
        Использует select_for_update для предотвращения дублирования при параллельных вызовах.
        
        Returns:
            tuple: (sent_count, failed_count) - количество клиентов, которым отправлено/не отправлено
        """
        from core.models_website import NotificationLog
        from django.db import transaction as db_transaction
        
        with db_transaction.atomic():
            already_notified_clients = set(
                NotificationLog.objects.select_for_update().filter(
                    container=container,
                    notification_type='PLANNED',
                    success=True
                ).values_list('client_id', flat=True)
            )
            
            clients = set()
            for car in container.container_cars.select_related('client').all():
                if car.client and car.client.has_notification_emails() and car.client.notification_enabled:
                    if car.client.id not in already_notified_clients:
                        clients.add(car.client)
            
            sent = 0
            failed = 0
            
            for client in clients:
                if ContainerNotificationService.send_planned_notification(container, client, user):
                    sent += 1
                else:
                    failed += 1
        
        return sent, failed
    
    @staticmethod
    def send_unload_to_all_clients(container, user=None):
        """
        Отправляет уведомление о разгрузке всем клиентам с автомобилями в контейнере.
        Использует select_for_update для предотвращения дублирования при параллельных вызовах.
        
        Returns:
            tuple: (sent_count, failed_count) - количество клиентов, которым отправлено/не отправлено
        """
        from core.models_website import NotificationLog
        from django.db import transaction as db_transaction
        
        with db_transaction.atomic():
            already_notified_clients = set(
                NotificationLog.objects.select_for_update().filter(
                    container=container,
                    notification_type='UNLOADED',
                    success=True
                ).values_list('client_id', flat=True)
            )
            
            clients = set()
            for car in container.container_cars.select_related('client').all():
                if car.client and car.client.has_notification_emails() and car.client.notification_enabled:
                    if car.client.id not in already_notified_clients:
                        clients.add(car.client)
            
            sent = 0
            failed = 0
            
            for client in clients:
                if ContainerNotificationService.send_unload_notification(container, client, user):
                    sent += 1
                else:
                    failed += 1
        
        return sent, failed
    
    @staticmethod
    def was_unload_notification_sent(container):
        """
        Проверяет, было ли уже отправлено уведомление о разгрузке для контейнера
        """
        from core.models_website import NotificationLog
        return NotificationLog.objects.filter(
            container=container,
            notification_type='UNLOADED',
            success=True
        ).exists()
    
    @staticmethod
    def was_planned_notification_sent(container):
        """
        Проверяет, было ли уже отправлено уведомление о планируемой разгрузке для контейнера
        """
        from core.models_website import NotificationLog
        return NotificationLog.objects.filter(
            container=container,
            notification_type='PLANNED',
            success=True
        ).exists()


class CarNotificationService:
    """
    Сервис для отправки уведомлений клиентам о разгрузке отдельных ТС (без контейнера).
    """
    
    @staticmethod
    def send_car_unload_notification(car, user=None):
        """
        Отправляет уведомление о разгрузке ТС (без контейнера) на все email клиента.
        
        Returns:
            bool: True если хотя бы одно письмо отправлено успешно
        """
        if not car.client:
            logger.warning(f"Cannot send car unload notification for {car.vin}: no client")
            return False
        
        client = car.client
        
        if not client.has_notification_emails() or not client.notification_enabled:
            logger.warning(f"Cannot send car unload notification to {client.name}: no emails or notifications disabled")
            return False
        
        if not car.unload_date:
            logger.warning(f"Cannot send car unload notification for {car.vin}: no unload_date set")
            return False
        
        car_info = {'vin': car.vin, 'brand': car.brand, 'year': car.year}
        
        _site_name, site_address = car.get_unload_address()
        
        context = {
            'car': car_info,
            'unload_date': car.unload_date,
            'warehouse': car.warehouse.name if car.warehouse else 'Не указан',
            'warehouse_address': site_address,
            'client_name': client.name,
            'company_name': getattr(settings, 'COMPANY_NAME', 'Caromoto Lithuania'),
            'company_phone': getattr(settings, 'COMPANY_PHONE', ''),
            'company_email': getattr(settings, 'COMPANY_EMAIL', ''),
            'company_website': getattr(settings, 'COMPANY_WEBSITE', ''),
        }
        
        subject = f"Ваш автомобиль {car.brand} ({car.vin}) разгружен"
        
        return CarNotificationService._send_notification_to_all_emails(
            car=car,
            client=client,
            subject=subject,
            template_name='email/car_unload_notification.html',
            context=context,
            car_info=car_info,
            user=user
        )
    
    @staticmethod
    def _send_notification_to_all_emails(car, client, subject, template_name, context, car_info, user=None):
        """
        Отправляет уведомление на все email-адреса клиента.
        """
        emails = client.get_notification_emails()
        
        if not emails:
            logger.warning(f"No emails found for client {client.name}")
            return False
        
        html_content = render_to_string(template_name, context)
        text_content = strip_tags(html_content)
        
        success_count = 0
        
        for email_to in emails:
            success = CarNotificationService._send_single_email(
                car=car,
                client=client,
                email_to=email_to,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
                car_info=car_info,
                user=user
            )
            if success:
                success_count += 1
        
        logger.info(f"Sent {success_count}/{len(emails)} car unload emails for {car.vin} to {client.name}")
        return success_count > 0
    
    @staticmethod
    def _send_single_email(car, client, email_to, subject, html_content, text_content, car_info, user=None):
        """
        Отправляет одно письмо и логирует результат.
        """
        from core.models_website import NotificationLog
        
        error_message = ''
        success = False
        
        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[email_to]
            )
            email.attach_alternative(html_content, "text/html")
            email.send(fail_silently=False)
            
            success = True
            logger.info(f"Email sent: CAR_UNLOADED for {car.vin} to {email_to}")
            
        except Exception as e:
            error_message = str(e)
            logger.error(f"Failed to send email: CAR_UNLOADED for {car.vin} to {email_to}: {e}")
        
        try:
            NotificationLog.objects.create(
                car=car,
                client=client,
                notification_type='CAR_UNLOADED',
                email_to=email_to,
                subject=subject,
                cars_info=json.dumps([car_info], ensure_ascii=False),
                success=success,
                error_message=error_message,
                created_by=user
            )
        except Exception as e:
            logger.error(f"Failed to create notification log: {e}")
        
        return success
    
    @staticmethod
    def was_car_unload_notification_sent(car):
        """
        Проверяет, было ли уже отправлено уведомление о разгрузке для этого ТС
        """
        from core.models_website import NotificationLog
        return NotificationLog.objects.filter(
            car=car,
            notification_type='CAR_UNLOADED',
            success=True
        ).exists()


