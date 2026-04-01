#!/usr/bin/env python3
"""
Скрипт для подготовки проекта к деплою на VPS
Создает архив с необходимыми файлами
"""

import os
import zipfile
from datetime import datetime
from pathlib import Path

# Директории и файлы для включения
INCLUDE_PATTERNS = [
    'core/',
    'logist2/',
    'templates/',
    'staticfiles/',
    'manage.py',
    'requirements.txt',
    'gunicorn_config.py',
    'deploy_vps.sh',
    'update_server.sh',
    'logist2.service',
    'nginx_logist2.conf',
    'env.production.example',
    'DEPLOY_INSTRUCTIONS.md',
    'DEPLOY_YOUR_SERVER.md',
]

# Исключить из архива
EXCLUDE_PATTERNS = [
    '__pycache__',
    '*.pyc',
    '*.pyo',
    '*.pyd',
    '.Python',
    'venv',
    '.venv',
    'env',
    '.env',
    '.git',
    '.gitignore',
    '*.sqlite3',
    '*.db',
    'db.sqlite3',
    '*.log',
    'node_modules',
    '.DS_Store',
    'Thumbs.db',
    '*.sql',  # Исключаем бэкапы БД
    '.idea',
    '.vscode',
    '*.md',  # Исключаем документацию кроме DEPLOY_INSTRUCTIONS
    '*.bat',
    '*.ps1',
]

def should_exclude(file_path):
    """Проверка, нужно ли исключить файл"""
    path_str = str(file_path)
    
    # Проверяем паттерны исключения
    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith('*'):
            if path_str.endswith(pattern[1:]):
                return True
        elif pattern in path_str:
            return True
    
    return False

def create_deployment_archive():
    """Создание архива для деплоя"""
    base_dir = Path(__file__).parent
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f'logist2_deploy_{timestamp}.zip'
    archive_path = base_dir / archive_name
    
    print(f"[*] Sozdanie arhiva dlya deploya: {archive_name}")
    print("=" * 60)
    
    files_added = 0
    
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Dobavlyaem fajly po patternам
        for pattern in INCLUDE_PATTERNS:
            path = base_dir / pattern
            
            if path.is_file():
                if not should_exclude(path):
                    arcname = path.relative_to(base_dir)
                    zipf.write(path, arcname)
                    files_added += 1
                    print(f"[+] Dobavlen fajl: {arcname}")
            
            elif path.is_dir():
                for file_path in path.rglob('*'):
                    if file_path.is_file() and not should_exclude(file_path):
                        arcname = file_path.relative_to(base_dir)
                        zipf.write(file_path, arcname)
                        files_added += 1
                        print(f"[+] Dobavlen fajl: {arcname}")
    
    archive_size = archive_path.stat().st_size / (1024 * 1024)  # MB
    
    print("=" * 60)
    print(f"[OK] Arhiv sozdan uspeshno!")
    print(f"[FILE] {archive_path}")
    print(f"[SIZE] {archive_size:.2f} MB")
    print(f"[FILES] Fajlov dobavleno: {files_added}")
    print()
    print("[NEXT STEPS]")
    print("1. Skopirujte arhiv na server:")
    print(f"   scp {archive_name} user@your-server:/var/www/")
    print()
    print("2. Na servere raspakujte:")
    print(f"   cd /var/www && unzip {archive_name}")
    print()
    print("3. Sledujte instrukciyam v DEPLOY_INSTRUCTIONS.md")
    
    return archive_path

if __name__ == '__main__':
    try:
        create_deployment_archive()
    except Exception as e:
        print(f"[ERROR] Oshibka pri sozdanii arhiva: {e}")
        exit(1)

