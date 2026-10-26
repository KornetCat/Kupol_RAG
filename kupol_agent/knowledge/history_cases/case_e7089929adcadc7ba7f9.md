# Исторический эпизод hist:e7089929adcadc7ba7f9: SENSOR_QUALITY

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-12T16:02:00+03:00 — 2026-10-12T16:18:00+03:00.
Подсистемы: telemetry_quality. Узлы: water_tank_01.
Признак: Источник помечен как неисправный/деградировавший; сопоставить независимые данные
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 16.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-12/telemetry.jsonl:962; 2026-10-12T16:01:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"water_tank_01": {"confidence": 0.972, "level_pct": 72.0, "online": true, "sensor_state": "OK", "state": "NORMAL", "status": "OK", "updated_at": 1791810060.0}}

Начало: Logs/2026-10-12/telemetry.jsonl:963; 2026-10-12T16:02:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"water_tank_01": {"confidence": 0.54, "level_pct": 71.7, "online": true, "sensor_state": "DEGRADED", "state": "NORMAL", "status": "OK", "updated_at": 1791810120.0}}

Последний кадр с признаком: Logs/2026-10-12/telemetry.jsonl:978; 2026-10-12T16:17:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"water_tank_01": {"confidence": 0.35, "level_pct": 71.7, "online": true, "sensor_state": "DEGRADED", "state": "NORMAL", "status": "OK", "updated_at": 1791811020.0}}

После: Logs/2026-10-12/telemetry.jsonl:979; 2026-10-12T16:18:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"water_tank_01": {"confidence": 0.975, "level_pct": 72.1, "online": true, "sensor_state": "OK", "state": "NORMAL", "status": "OK", "updated_at": 1791811080.0}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
Связанная команда в окне ±5 минут не найдена. Причина исчезновения признака неизвестна.
