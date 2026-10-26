# Исторический эпизод hist:d6ace6fddd1764853a90: SENSOR_QUALITY

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-14T13:41:00+03:00 — 2026-10-14T14:10:00+03:00.
Подсистемы: telemetry_quality. Узлы: climate_sensor_03.
Признак: Источник помечен как неисправный/деградировавший; сопоставить независимые данные
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 29.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-14/telemetry.jsonl:821; 2026-10-14T13:40:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"climate_sensor_03": {"co2_ppm": 807, "confidence": 0.671, "humidity_pct": 48.2, "online": true, "reading_delay_s": 1800.0, "sensor_state": "OK", "status": "OK", "temperature_c": 21.28, "updated_at": 1791974400.0}}

Начало: Logs/2026-10-14/telemetry.jsonl:822; 2026-10-14T13:41:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"climate_sensor_03": {"co2_ppm": 835, "confidence": 0.52, "humidity_pct": 47.3, "online": true, "reading_delay_s": 1800.0, "sensor_state": "DEGRADED", "status": "OK", "temperature_c": 21.24, "updated_at": 1791974460.0}}

Последний кадр с признаком: Logs/2026-10-14/telemetry.jsonl:850; 2026-10-14T14:09:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"climate_sensor_03": {"co2_ppm": 800, "confidence": 0.37, "humidity_pct": 45.3, "online": true, "reading_delay_s": 1800.0, "sensor_state": "DEGRADED", "status": "OK", "temperature_c": 21.01, "updated_at": 1791976140.0}}

После: Logs/2026-10-14/telemetry.jsonl:851; 2026-10-14T14:10:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"climate_sensor_03": {"co2_ppm": 788, "confidence": 0.682, "humidity_pct": 48.0, "online": true, "reading_delay_s": 1800.0, "sensor_state": "OK", "status": "OK", "temperature_c": 21.34, "updated_at": 1791976200.0}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
Связанная команда в окне ±5 минут не найдена. Причина исчезновения признака неизвестна.
