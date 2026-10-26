# Исторический эпизод hist:cbd96f0d66359e8ac9e6: DATA_UNAVAILABLE

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-12T10:43:00+03:00 — 2026-10-12T10:44:00+03:00.
Подсистемы: telemetry_quality, communications, power_distribution. Узлы: thermal_insulation_01.
Признак: Потеря, заморозка или пустые показания; сначала общая связь и питание
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 1.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-12/telemetry.jsonl:643; 2026-10-12T10:42:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [46, 46, 46], "online": true, "status": "OK", "temperature_c": [22.92, 23.12, 21.31], "updated_at": 1791790920.0, "voc_index": null}}

Начало: Logs/2026-10-12/telemetry.jsonl:644; 2026-10-12T10:43:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [46, 47, 45], "online": true, "status": "OK", "temperature_c": null, "updated_at": 1791790980.0, "voc_index": [39, 182]}}

Последний кадр с признаком: Logs/2026-10-12/telemetry.jsonl:644; 2026-10-12T10:43:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [46, 47, 45], "online": true, "status": "OK", "temperature_c": null, "updated_at": 1791790980.0, "voc_index": [39, 182]}}

После: Logs/2026-10-12/telemetry.jsonl:645; 2026-10-12T10:44:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": null, "online": true, "status": "OK", "temperature_c": [23.11, 23.36, 21.49], "updated_at": 1791791040.0, "voc_index": null}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
Связанная команда в окне ±5 минут не найдена. Причина исчезновения признака неизвестна.
