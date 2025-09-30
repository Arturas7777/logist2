#!/usr/bin/env python3
"""
Скрипт для создания файла .env с настройками PostgreSQL
"""

import os
from pathlib import Path

def create_env_file():
    """Создает файл .env с настройками для разработки"""
    
    env_content = """# Django Settings
SECRET_KEY=django-insecure-development-key-change-in-production
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# PostgreSQL Database
DB_NAME=logist2
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432

# Redis for Channels
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
"""
    
    env_file = Path('.env')
    
    if env_file.exists():
        print("⚠️  Файл .env уже существует!")
        response = input("Перезаписать? (y/N): ").lower().strip()
        if response != 'y':
            print("❌ Файл .env не изменен")
            return False
    
    try:
        with open(env_file, 'w', encoding='utf-8') as f:
            f.write(env_content)
        
        print("✅ Файл .env создан успешно!")
        print("📝 Содержимое:")
        print("-" * 40)
        print(env_content)
        print("-" * 40)
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка создания файла .env: {e}")
        return False

def main():
    """Основная функция"""
    print("🔧 Создание файла .env для проекта Logist2")
    print("=" * 50)
    
    # Проверяем, что мы в правильной директории
    if not Path('manage.py').exists():
        print("❌ Ошибка: файл manage.py не найден!")
        print("   Запустите скрипт из корневой папки проекта")
        return
    
    # Создаем файл .env
    if create_env_file():
        print("\n🎉 Готово! Теперь вы можете:")
        print("1. Запустить проект: python start_simple.py")
        print("2. Или использовать: START_ME.bat / START_ME.ps1")
        print("\n💡 Убедитесь, что PostgreSQL запущен!")
    else:
        print("\n❌ Не удалось создать файл .env")
        print("   Создайте его вручную по образцу выше")

if __name__ == '__main__':
    main()


