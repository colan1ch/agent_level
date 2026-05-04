# Coursework LLM Agent

FastAPI-based agent для работы с локальной LLM (Ollama) с накоплением полного ответа и разбиением на сегменты.

## Архитектура

```
Client (POST /ask)
  ↓
FastAPI Agent (Python)
  ├→ Ollama (get full response)
  ├→ Split into 105-byte segments
  └→ Transport Layer (Go) × N segments
  ↓
returns {"status": "ok", "segments_sent": N}
```

## Ключевые особенности

- **Полный ответ** — Ollama возвращает полный текст (не streaming)
- **Фиксированные сегменты** — 105 байт на сегмент (для демонстрации)
- **Известное количество** — `total_segments` известно заранее
- **Асинхронная отправка** — httpx.AsyncClient для параллельной обработки
- **Логирование** — подробное логирование каждого сегмента

## Требования

- Python 3.11+
- Ollama (установлена локально)
- Go транспортный слой на 10.241.116.6:8080

## Установка

```bash
pip install -r requirements.txt
```

## Запуск

**1. Запусти Ollama:**
```bash
ollama serve
```

**2. Запусти FastAPI агент:**
```bash
python main.py
```

Сервис доступен на `http://0.0.0.0:8000`
Swagger Docs: `http://localhost:8000/docs`

## Поток работы

1. Клиент отправляет `POST /ask` с документацией и вопросом
2. Агент запрашивает Ollama и получает **полный ответ** целиком
3. Ответ разбивается на сегменты по **105 байт**
4. Каждый сегмент отправляется на Go слой (`POST /segment`)
5. После всех сегментов агент возвращает: `{"status": "ok", "segments_sent": N}`

## API

### POST /ask

**Запрос:**
```json
{
  "documentation": "Функция calculate() выполняет вычисления по формуле",
  "question": "Как работает функция calculate?"
}
```

**Ответ (200 OK):**
```json
{
  "status": "ok",
  "segments_sent": 4,
  "message_id": "1746355227_a1b2c3d4"
}
```

**Ошибка (500):**
```json
{
  "error": true,
  "message": "Cannot connect to Ollama. Make sure it's running on localhost:11434"
}
```

### GET /health

**Ответ:**
```json
{
  "status": "ok"}
```

## Сегментация

Текст разбивается на сегменты размером **105 байт**.

**Пример:**
- Если ответ 420 байт → 4 сегмента (105 + 105 + 105 + 105 байт)
- Каждый сегмент отправляется на `/segment` с информацией:

```json
{
  "message_id": "1746355227_a1b2c3d4",
  "total_segments": 4,
  "segment_number": 1,
  "payload": "первые 105 байт текста..."
}
```

**Поля:**
- `message_id` — уникальный ID запроса
- `total_segments` — общее количество сегментов
- `segment_number` — номер сегмента (1, 2, 3, ...)
- `payload` — текст сегмента (UTF-8, до 105 байт)

## Примеры использования

### cURL
```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "documentation": "Функция sum(a, b) складывает два числа",
    "question": "Как работает sum()?"
  }'
```

**Ответ:**
```json
{
  "status": "ok",
  "segments_sent": 3,
  "message_id": "1746355227_xyz123ab"
}
```

### Python
```python
import requests
import json

response = requests.post(
    "http://localhost:8000/ask",
    json={
        "documentation": "User имеет поля: id, name, email",
        "question": "Какая структура у User?"
    }
)

print(json.dumps(response.json(), indent=2))
```

### JavaScript
```javascript
const response = await fetch("http://localhost:8000/ask", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    documentation: "API возвращает JSON",
    question: "Какой формат ответа?"
  })
});

const result = await response.json();
console.log(result);
```

## Логирование

**Информационные сообщения:**
```
2026-05-02 12:34:56,789 - __main__ - INFO - Processing request with message_id: 1746355227_a1b2c3d4
2026-05-02 12:34:57,123 - __main__ - INFO - Got full response from Ollama: 420 characters
2026-05-02 12:34:57,456 - __main__ - INFO - Split response into 4 segments (size: 105 bytes each)
2026-05-02 12:34:57,789 - __main__ - INFO - Segment 1/4 sent (msg: 1746355227_a1b2c3d4, size: 105 bytes)
2026-05-02 12:34:58,012 - __main__ - INFO - Message 1746355227_a1b2c3d4: Successfully sent 4/4 segments
```

**Ошибки:**
```
2026-05-02 12:35:00,000 - __main__ - ERROR - Connection to Ollama failed: Connection refused
2026-05-02 12:35:01,000 - __main__ - ERROR - Transport layer returned 502 for segment 2
```

## Обработка ошибок

| Ситуация | Поведение |
|----------|-----------|
| Ollama недоступна | HTTP 500 с ошибкой |
| Ollama возвращает пусто | HTTP 500 с ошибкой |
| Таймаут Ollama (>60 сек) | HTTP 500 с ошибкой |
| Ошибка отправки сегмента | Логируется, не прерывает |

## Структура кода

```python
get_full_response_from_ollama()  # Получает полный ответ
split_into_segments()            # Разбивает на 105-байтовые сегменты
send_segments_to_transport()     # Отправляет на Go слой
process_request()                # Координирует весь поток
```

## Настройка

Отредактируй `main.py` для изменения:

```python
OLLAMA_API_URL = "http://localhost:11434/api/generate"  # Ollama API
OLLAMA_MODEL = "qwen2.5:7b"                            # Модель
TRANSPORT_API_URL = "http://10.241.116.6:8080/segment" # Go слой
REQUEST_TIMEOUT = 60.0                                   # Таймаут Ollama (сек)
SEGMENT_SIZE = 105                                       # Размер сегмента (байт)
```

## Производительность

- **Накопление ответа** — гарантирует знание точного размера
- **Фиксированные сегменты** — предсказуемое разбиение
- **Асинхронная отправка** — httpx.AsyncClient обрабатывает несколько сегментов
- **Обработка UTF-8** — корректное разбиение многобайтовых символов

## Лицензия

MIT


