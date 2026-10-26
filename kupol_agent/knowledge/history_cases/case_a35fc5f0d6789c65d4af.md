# Исторический эпизод hist:a35fc5f0d6789c65d4af: DATA_UNAVAILABLE

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-12T11:03:00+03:00 — 2026-10-12T11:04:00+03:00.
Подсистемы: telemetry_quality, communications, power_distribution. Узлы: thermal_insulation_01.
Признак: Потеря, заморозка или пустые показания; сначала общая связь и питание
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 1.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-12/telemetry.jsonl:663; 2026-10-12T11:02:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [50, 48, 45], "online": true, "status": "OK", "temperature_c": [22.87, 23.27, 21.42], "updated_at": 1791792120.0, "voc_index": null}}

Начало: Logs/2026-10-12/telemetry.jsonl:664; 2026-10-12T11:03:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": null, "online": true, "status": "OK", "temperature_c": null, "updated_at": 1791792180.0, "voc_index": [44, 184]}}

Последний кадр с признаком: Logs/2026-10-12/telemetry.jsonl:664; 2026-10-12T11:03:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": null, "online": true, "status": "OK", "temperature_c": null, "updated_at": 1791792180.0, "voc_index": [44, 184]}}

После: Logs/2026-10-12/telemetry.jsonl:665; 2026-10-12T11:04:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": null, "online": true, "status": "OK", "temperature_c": [23.26, 23.41, 21.41], "updated_at": 1791792240.0, "voc_index": [48, 185]}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
Связанная команда в окне ±5 минут не найдена. Причина исчезновения признака неизвестна.
