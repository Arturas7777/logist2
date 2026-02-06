# Транскрипция аудио — памятка для Claude

## Что нужно для работы

Окружение использует скрипт `transcribe_audio.py`:
- **openai-whisper** — распознавание речи
- **PyTorch с CUDA** — GPU-ускорение (NVIDIA, опционально)
- **FFmpeg** — декодирование медиафайлов
- **whisperx** — diarization (опционально)
- **python-docx** — только если нужно выгружать в Word (устанавливается отдельно)

---

## Команды для запуска транскрипции

Дать пользователю эти команды:

```powershell
# 1. Подготовка (выполнить один раз при открытии терминала)
& c:/Users/art-f/PycharmProjects/logist2/.venv/Scripts/Activate.ps1
$env:PATH += ";C:\Users\art-f\PycharmProjects\logist2\.venv\Lib\site-packages\imageio_ffmpeg\binaries"
cd c:\Users\art-f\PycharmProjects\logist2

# 2. Запуск транскрипции
python transcribe_audio.py "ПУТЬ_К_ФАЙЛУ"
```

---

## Параметры скрипта

- `--model large` — лучшее качество (по умолчанию medium)
- `--clean` — убрать слова-паразиты
- `--diarize --hf-token <TOKEN>` — разделение по спикерам (нужен `whisperx` и HF токен)
- `--output <FILE>` — сохранить результат в конкретный файл

---

## После транскрипции

Пользователь попросит обработать текст. Что делать:

1. **Прочитать файл .txt** (результат транскрипции)
2. **Убрать воду:** таймкоды, слова-паразиты, технические разговоры
3. **Разбить по спикерам:** Интервьюер / Герой
4. **Структурировать по темам**
5. **Создать docx** если нужно (использовать `python-docx`, установить отдельно)

---

## Пути

- Скрипт: `c:\Users\art-f\PycharmProjects\logist2\transcribe_audio.py`
- Результаты обычно в: `c:\Users\art-f\OneDrive\Desktop\TRANSCRIPTIONS\`

---

## Если CUDA не работает

```powershell
pip install torch==2.7.1 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118 --force-reinstall
```
