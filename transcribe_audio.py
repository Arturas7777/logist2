"""
Скрипт для транскрипции аудио/видео файлов с помощью OpenAI Whisper.
Поддерживает разделение по спикерам (diarization).

Использование:
    python transcribe_audio.py путь_к_файлу.mp3
    python transcribe_audio.py путь_к_файлу.mp4 --model medium
    python transcribe_audio.py путь_к_файлу.wav --diarize
"""

import argparse
import os
import sys
from pathlib import Path


def check_dependencies():
    """Проверяет установленные зависимости."""
    missing = []
    
    try:
        import whisper
    except ImportError:
        missing.append("openai-whisper")
    
    try:
        import torch
    except ImportError:
        missing.append("torch")
    
    if missing:
        print("❌ Не установлены необходимые пакеты:")
        print(f"   pip install {' '.join(missing)}")
        print("\nДля GPU-ускорения (NVIDIA):")
        print("   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118")
        sys.exit(1)
    
    return True


def transcribe_simple(audio_path: str, model_name: str = "medium", language: str = "ru"):
    """
    Простая транскрипция без разделения по спикерам.
    
    Модели Whisper (от быстрой к точной):
    - tiny: ~1GB VRAM, быстрая, низкое качество
    - base: ~1GB VRAM
    - small: ~2GB VRAM
    - medium: ~5GB VRAM (рекомендуется)
    - large: ~10GB VRAM, лучшее качество
    """
    import whisper
    import torch
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"🔧 Устройство: {device.upper()}")
    print(f"📥 Загрузка модели '{model_name}'...")
    
    model = whisper.load_model(model_name, device=device)
    
    print(f"🎤 Транскрибирую: {audio_path}")
    print("   Это может занять несколько минут...")
    
    result = model.transcribe(
        audio_path,
        language=language,
        verbose=False,
        task="transcribe"
    )
    
    return result


def transcribe_with_diarization(audio_path: str, model_name: str = "medium", language: str = "ru"):
    """
    Транскрипция с разделением по спикерам (требует whisperx).
    
    Для использования нужно:
    1. pip install whisperx
    2. Создать аккаунт на huggingface.co
    3. Принять условия использования модели pyannote:
       - https://huggingface.co/pyannote/speaker-diarization
       - https://huggingface.co/pyannote/segmentation
    4. Создать токен: https://huggingface.co/settings/tokens
    5. Установить переменную окружения HF_TOKEN или передать через --hf-token
    """
    try:
        import whisperx
        import torch
    except ImportError:
        print("❌ Для diarization нужен whisperx:")
        print("   pip install whisperx")
        sys.exit(1)
    
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        print("❌ Не найден HuggingFace токен.")
        print("   Установите переменную окружения HF_TOKEN")
        print("   или используйте --hf-token YOUR_TOKEN")
        sys.exit(1)
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    
    print(f"🔧 Устройство: {device.upper()}")
    print(f"📥 Загрузка модели '{model_name}'...")
    
    # Загрузка и транскрипция
    model = whisperx.load_model(model_name, device, compute_type=compute_type, language=language)
    audio = whisperx.load_audio(audio_path)
    
    print(f"🎤 Транскрибирую: {audio_path}")
    result = model.transcribe(audio, batch_size=16)
    
    # Выравнивание
    print("📐 Выравнивание по словам...")
    model_a, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result["segments"], model_a, metadata, audio, device, return_char_alignments=False)
    
    # Diarization
    print("👥 Определение спикеров...")
    diarize_model = whisperx.DiarizationPipeline(use_auth_token=hf_token, device=device)
    diarize_segments = diarize_model(audio)
    result = whisperx.assign_word_speakers(diarize_segments, result)
    
    return result


def format_output(result, with_diarization=False):
    """Форматирует результат в читаемый текст."""
    output_lines = []
    
    if with_diarization:
        current_speaker = None
        current_text = []
        
        for segment in result.get("segments", []):
            speaker = segment.get("speaker", "UNKNOWN")
            text = segment.get("text", "").strip()
            
            if speaker != current_speaker:
                if current_text:
                    output_lines.append(f"\n[{current_speaker}]: {' '.join(current_text)}")
                current_speaker = speaker
                current_text = [text] if text else []
            else:
                if text:
                    current_text.append(text)
        
        # Последний спикер
        if current_text:
            output_lines.append(f"\n[{current_speaker}]: {' '.join(current_text)}")
    else:
        # Простой формат с таймкодами
        for segment in result.get("segments", []):
            start = segment.get("start", 0)
            end = segment.get("end", 0)
            text = segment.get("text", "").strip()
            
            start_time = f"{int(start//60):02d}:{int(start%60):02d}"
            end_time = f"{int(end//60):02d}:{int(end%60):02d}"
            
            output_lines.append(f"[{start_time} - {end_time}] {text}")
    
    return "\n".join(output_lines)


def clean_transcript(text: str) -> str:
    """
    Базовая очистка текста от слов-паразитов.
    Для более глубокой обработки лучше использовать LLM.
    """
    import re
    
    # Слова-паразиты для удаления
    filler_words = [
        r'\bэээ+\b', r'\bааа+\b', r'\bммм+\b', r'\bугу\b',
        r'\bну\b', r'\bвот\b', r'\bтипа\b', r'\bкороче\b',
        r'\bкак бы\b', r'\bто есть\b', r'\bв общем\b',
        r'\bтак сказать\b', r'\bпонимаешь\b', r'\bзначит\b',
    ]
    
    cleaned = text
    for pattern in filler_words:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # Убираем множественные пробелы
    cleaned = re.sub(r'\s+', ' ', cleaned)
    cleaned = re.sub(r'\s+([.,!?])', r'\1', cleaned)
    
    return cleaned.strip()


def main():
    parser = argparse.ArgumentParser(
        description="Транскрипция аудио/видео с помощью Whisper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python transcribe_audio.py interview.mp3
  python transcribe_audio.py video.mp4 --model large
  python transcribe_audio.py audio.wav --diarize --hf-token YOUR_TOKEN
  python transcribe_audio.py audio.mp3 --clean
        """
    )
    
    parser.add_argument("audio_file", help="Путь к аудио/видео файлу")
    parser.add_argument("--model", "-m", default="medium",
                        choices=["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"],
                        help="Модель Whisper (default: medium)")
    parser.add_argument("--language", "-l", default="ru",
                        help="Язык аудио (default: ru)")
    parser.add_argument("--diarize", "-d", action="store_true",
                        help="Разделить по спикерам (требует whisperx)")
    parser.add_argument("--hf-token", help="HuggingFace токен для diarization")
    parser.add_argument("--output", "-o", help="Файл для сохранения результата")
    parser.add_argument("--clean", "-c", action="store_true",
                        help="Убрать слова-паразиты")
    
    args = parser.parse_args()
    
    # Проверка файла
    if not os.path.exists(args.audio_file):
        print(f"❌ Файл не найден: {args.audio_file}")
        sys.exit(1)
    
    # Установка токена если передан
    if args.hf_token:
        os.environ["HF_TOKEN"] = args.hf_token
    
    # Проверка зависимостей
    check_dependencies()
    
    # Транскрипция
    try:
        if args.diarize:
            result = transcribe_with_diarization(args.audio_file, args.model, args.language)
        else:
            result = transcribe_simple(args.audio_file, args.model, args.language)
    except Exception as e:
        print(f"❌ Ошибка транскрипции: {e}")
        sys.exit(1)
    
    # Форматирование
    formatted = format_output(result, with_diarization=args.diarize)
    
    # Очистка от слов-паразитов
    if args.clean:
        formatted = clean_transcript(formatted)
    
    # Вывод/сохранение
    if args.output:
        output_path = args.output
    else:
        input_path = Path(args.audio_file)
        output_path = input_path.with_suffix(".txt")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(formatted)
    
    print(f"\n✅ Готово! Результат сохранён в: {output_path}")
    print(f"📄 Размер: {len(formatted)} символов")
    
    # Показать превью
    preview = formatted[:500] + "..." if len(formatted) > 500 else formatted
    print(f"\n📝 Превью:\n{'-'*50}\n{preview}\n{'-'*50}")


if __name__ == "__main__":
    main()
