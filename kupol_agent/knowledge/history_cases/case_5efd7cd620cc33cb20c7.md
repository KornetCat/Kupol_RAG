# Исторический эпизод hist:5efd7cd620cc33cb20c7: SENSOR_QUALITY

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-14T07:18:00+03:00 — 2026-10-14T07:55:00+03:00.
Подсистемы: telemetry_quality. Узлы: climate_sensor_02.
Признак: Источник помечен как неисправный/деградировавший; сопоставить независимые данные
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 37.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-14/telemetry.jsonl:438; 2026-10-14T07:17:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"climate_sensor_02": {"co2_ppm": 800, "confidence": 0.72, "humidity_pct": 54.0, "online": true, "reading_delay_s": 900.0, "sensor_state": "OK", "status": "OK", "temperature_c": 20.19, "updated_at": 1791951420.0}}

Начало: Logs/2026-10-14/telemetry.jsonl:439; 2026-10-14T07:18:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"climate_sensor_02": {"co2_ppm": 830, "confidence": 0.46, "humidity_pct": 50.6, "online": true, "reading_delay_s": 900.0, "sensor_state": "DEGRADED", "status": "OK", "temperature_c": 19.98, "updated_at": 1791951480.0}}

Последний кадр с признаком: Logs/2026-10-14/telemetry.jsonl:475; 2026-10-14T07:54:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"climate_sensor_02": {"co2_ppm": 862, "confidence": 0.42, "humidity_pct": 46.5, "online": true, "reading_delay_s": 900.0, "sensor_state": "DEGRADED", "status": "OK", "temperature_c": 20.65, "updated_at": 1791953640.0}}

После: Logs/2026-10-14/telemetry.jsonl:476; 2026-10-14T07:55:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"climate_sensor_02": {"co2_ppm": 800, "confidence": 0.723, "humidity_pct": 52.5, "online": true, "reading_delay_s": 900.0, "sensor_state": "OK", "status": "OK", "temperature_c": 20.18, "updated_at": 1791953700.0}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
Связанная команда в окне ±5 минут не найдена. Причина исчезновения признака неизвестна.
