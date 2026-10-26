# Исторический эпизод hist:e4ddd8a92107ca5ae144: DATA_UNAVAILABLE

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-18T13:31:00+03:00 — 2026-10-18T16:19:00+03:00.
Подсистемы: telemetry_quality, communications, power_distribution. Узлы: weather_station_01.
Признак: Потеря, заморозка или пустые показания; сначала общая связь и питание
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 168.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-18/telemetry.jsonl:811; 2026-10-18T13:30:00+03:00; SHA256 5b9d9ade36bdf1f133cc177c0fdc89dd12102392660df03a29de9787b19bcb5b.
{"weather_station_01": {"confidence": 0.959, "humidity_pct": 62.5, "online": true, "sensor_state": "OK", "status": "OK", "temperature_c": 26.5, "updated_at": 1792319400.0}}

Начало: Logs/2026-10-18/telemetry.jsonl:812; 2026-10-18T13:31:00+03:00; SHA256 5b9d9ade36bdf1f133cc177c0fdc89dd12102392660df03a29de9787b19bcb5b.
{"weather_station_01": {"confidence": 0.05, "humidity_pct": null, "online": true, "sensor_state": "FAULT", "status": "OK", "temperature_c": null, "updated_at": 1792319460.0}}

Последний кадр с признаком: Logs/2026-10-18/telemetry.jsonl:979; 2026-10-18T16:18:00+03:00; SHA256 5b9d9ade36bdf1f133cc177c0fdc89dd12102392660df03a29de9787b19bcb5b.
{"weather_station_01": {"confidence": 0.05, "humidity_pct": null, "online": true, "sensor_state": "FAULT", "status": "OK", "temperature_c": null, "updated_at": 1792329480.0}}

После: Logs/2026-10-18/telemetry.jsonl:980; 2026-10-18T16:19:00+03:00; SHA256 5b9d9ade36bdf1f133cc177c0fdc89dd12102392660df03a29de9787b19bcb5b.
{"weather_station_01": {"confidence": 0.979, "humidity_pct": 56.3, "online": true, "sensor_state": "OK", "status": "OK", "temperature_c": 23.9, "updated_at": 1792329540.0}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
Связанная команда в окне ±5 минут не найдена. Причина исчезновения признака неизвестна.
