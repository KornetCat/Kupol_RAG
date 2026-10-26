# Исторический эпизод hist:8cc79da323da5d9beea9: DATA_UNAVAILABLE

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-12T11:16:00+03:00 — 2026-10-12T11:17:00+03:00.
Подсистемы: telemetry_quality, communications, power_distribution. Узлы: thermal_insulation_01.
Признак: Потеря, заморозка или пустые показания; сначала общая связь и питание
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 1.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-12/telemetry.jsonl:676; 2026-10-12T11:15:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [39, 38, 46], "online": true, "status": "OK", "temperature_c": [23.65, 23.94, 21.49], "updated_at": 1791792900.0, "voc_index": null}}

Начало: Logs/2026-10-12/telemetry.jsonl:677; 2026-10-12T11:16:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [34, 34, 45], "online": true, "status": "OK", "temperature_c": null, "updated_at": 1791792960.0, "voc_index": [45, 174]}}

Последний кадр с признаком: Logs/2026-10-12/telemetry.jsonl:677; 2026-10-12T11:16:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [34, 34, 45], "online": true, "status": "OK", "temperature_c": null, "updated_at": 1791792960.0, "voc_index": [45, 174]}}

После: Logs/2026-10-12/telemetry.jsonl:678; 2026-10-12T11:17:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [33, 32, 46], "online": true, "status": "OK", "temperature_c": [22.98, 23.38, 21.44], "updated_at": 1791793020.0, "voc_index": null}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
Связанная команда в окне ±5 минут не найдена. Причина исчезновения признака неизвестна.
