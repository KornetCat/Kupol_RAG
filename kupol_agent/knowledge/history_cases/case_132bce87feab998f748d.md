# Исторический эпизод hist:132bce87feab998f748d: SENSOR_QUALITY

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-12T05:03:00+03:00 — 2026-10-12T05:34:00+03:00.
Подсистемы: telemetry_quality. Узлы: climate_sensor_03.
Признак: Источник помечен как неисправный/деградировавший; сопоставить независимые данные
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 31.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-12/telemetry.jsonl:303; 2026-10-12T05:02:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"climate_sensor_03": {"co2_ppm": 780, "confidence": 0.875, "humidity_pct": 51.4, "online": true, "reading_delay_s": 1800.0, "sensor_state": "OK", "status": "OK", "temperature_c": 20.93, "updated_at": 1791770520.0}}

Начало: Logs/2026-10-12/telemetry.jsonl:304; 2026-10-12T05:03:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"climate_sensor_03": {"co2_ppm": 772, "confidence": 0.55, "humidity_pct": 54.3, "online": true, "reading_delay_s": 1800.0, "sensor_state": "DEGRADED", "status": "OK", "temperature_c": 21.07, "updated_at": 1791770580.0}}

Последний кадр с признаком: Logs/2026-10-12/telemetry.jsonl:334; 2026-10-12T05:33:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"climate_sensor_03": {"co2_ppm": 777, "confidence": 0.42, "humidity_pct": 50.5, "online": true, "reading_delay_s": 1800.0, "sensor_state": "DEGRADED", "status": "OK", "temperature_c": 20.9, "updated_at": 1791772380.0}}

После: Logs/2026-10-12/telemetry.jsonl:335; 2026-10-12T05:34:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"climate_sensor_03": {"co2_ppm": 773, "confidence": 0.915, "humidity_pct": 52.2, "online": true, "reading_delay_s": 1800.0, "sensor_state": "OK", "status": "OK", "temperature_c": 20.67, "updated_at": 1791772440.0}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
Связанная команда в окне ±5 минут не найдена. Причина исчезновения признака неизвестна.
