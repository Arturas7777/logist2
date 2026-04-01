"""
Скрипт для создания тестового клиента
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'logist2.settings')
django.setup()

from django.contrib.auth.models import User
from core.models import Client
from core.models_website import ClientUser

# Создаем пользователя
username = 'test_client'
email = 'test@caromoto-lt.com'
password = 'test123456'

# Проверяем, существует ли пользователь
if User.objects.filter(username=username).exists():
    print(f"[!] Пользователь '{username}' уже существует")
    user = User.objects.get(username=username)
else:
    user = User.objects.create_user(username=username, email=email, password=password)
    print(f"[+] Создан пользователь: {username}")
    print(f"    Email: {email}")
    print(f"    Пароль: {password}")

# Берем первого клиента или создаем нового
try:
    client = Client.objects.first()
    if not client:
        client = Client.objects.create(name='Тестовый клиент')
        print(f"[+] Создан клиент: {client.name}")
    else:
        print(f"[+] Используется существующий клиент: {client.name}")
except Exception as e:
    client = Client.objects.create(name='Тестовый клиент')
    print(f"[+] Создан клиент: {client.name}")

# Связываем пользователя с клиентом
if ClientUser.objects.filter(user=user).exists():
    print(f"[!] ClientUser для '{username}' уже существует")
else:
    ClientUser.objects.create(
        user=user,
        client=client,
        phone='+370123456789',
        language='ru',
        is_verified=True
    )
    print(f"[+] Создан ClientUser для '{username}'")

print("\n" + "="*60)
print("ТЕСТОВЫЙ КЛИЕНТ УСПЕШНО СОЗДАН!")
print("="*60)
print(f"\nДанные для входа:")
print(f"   URL: http://localhost:8000/dashboard/")
print(f"   Username: {username}")
print(f"   Password: {password}")
print(f"\nДля входа в админку: http://localhost:8000/admin/")
print("="*60)

