"""
ReceiptParserService
====================
Parses receipt/check photos using Anthropic Claude Vision API.
Extracts store name, items, prices, and stores result in Transaction.receipt_data.
"""

import base64
import json
import logging
import os

logger = logging.getLogger(__name__)

RECEIPT_SCHEMA = """{
  "store_name": "название магазина/заведения или null",
  "date": "YYYY-MM-DD или null",
  "items": [
    {"name": "название товара/услуги", "qty": 1, "price": 0.00}
  ],
  "total": 0.00,
  "currency": "EUR",
  "ai_summary": "краткое описание покупки в 5-10 слов (напр. 'Продукты: молочные, хлеб, овощи')"
}"""

SYSTEM_PROMPT = f"""Ты — система распознавания кассовых чеков из магазинов и заведений.
Твоя задача: извлечь структурированные данные из фото чека.

Правила:
- Извлеки ВСЕ позиции из чека (название, количество, цена за единицу).
- Если количество не указано — считай 1.
- price — цена за единицу товара (НЕ общая сумма позиции).
- total — итоговая сумма чека (ищи слова: TOTAL, ИТОГО, VISO, SUMA, IŠ VISO и т.п.).
- Определи валюту по символу (€=EUR, $=USD, £=GBP) или стране магазина. По умолчанию EUR.
- store_name — название магазина/сети (Lidl, Maxima, IKI, Rimi, Bolt Food и т.п.).
- date — дата покупки, если указана на чеке.
- ai_summary — краткая категоризация: что было куплено (напр. "Продукты: мясо, овощи, напитки").
- Если чек нечитаемый или это не чек — верни {{"error": "Не удалось распознать чек", "items": [], "total": null}}.
- НЕ выдумывай данных — только то, что видно на фото.

Верни ТОЛЬКО валидный JSON по этой схеме:
{RECEIPT_SCHEMA}"""


def _parse_json_response(text: str) -> dict:
    """Strip markdown fences and parse JSON."""
    text = text.strip()
    if text.startswith('```'):
        lines = text.split('\n')
        lines = [l for l in lines if not l.strip().startswith('```')]
        text = '\n'.join(lines)
    return json.loads(text)


def _read_image_as_base64(file_path: str) -> tuple[str, str]:
    """Read image file and return (base64_data, media_type)."""
    ext = os.path.splitext(file_path)[1].lower()
    media_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
    }
    media_type = media_types.get(ext, 'image/jpeg')

    with open(file_path, 'rb') as f:
        data = base64.b64encode(f.read()).decode('utf-8')
    return data, media_type


def parse_receipt_image(image_path: str) -> dict:
    """
    Send a receipt image to Claude Vision and get structured data back.
    Returns dict with store_name, items, total, etc.
    """
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic not installed")
        raise

    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    image_data, media_type = _read_image_as_base64(image_path)

    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                },
                {
                    "type": "text",
                    "text": "Распознай этот кассовый чек. Верни ТОЛЬКО JSON.",
                },
            ],
        }],
    )

    return _parse_json_response(response.content[0].text)


def parse_transaction_receipt(transaction_id: int) -> dict | None:
    """
    Parse the receipt attached to a Transaction and save result to receipt_data.
    Returns the parsed data or None if no attachment / parsing failed.
    """
    from core.models_billing import Transaction

    try:
        tx = Transaction.objects.get(id=transaction_id)
    except Transaction.DoesNotExist:
        logger.warning("Transaction %d not found", transaction_id)
        return None

    if not tx.attachment:
        logger.info("Transaction %d has no attachment", transaction_id)
        return None

    try:
        file_path = tx.attachment.path
        result = parse_receipt_image(file_path)
        tx.receipt_data = result
        tx.save(update_fields=['receipt_data'])
        logger.info("Receipt parsed for transaction %d: %s", transaction_id,
                     result.get('ai_summary', ''))
        return result
    except Exception as e:
        logger.error("Failed to parse receipt for transaction %d: %s",
                     transaction_id, e, exc_info=True)
        tx.receipt_data = {"error": str(e), "items": [], "total": None}
        tx.save(update_fields=['receipt_data'])
        return None
