# Исторический эпизод hist:ee07abc243a4d1bead31: DATA_UNAVAILABLE

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-12T12:03:00+03:00 — 2026-10-12T12:04:00+03:00.
Подсистемы: telemetry_quality, communications, power_distribution. Узлы: thermal_insulation_01.
Признак: Потеря, заморозка или пустые показания; сначала общая связь и питание
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 1.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-12/telemetry.jsonl:723; 2026-10-12T12:02:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [57, 57, 44], "online": true, "status": "OK", "temperature_c": [22.66, 23.02, 21.47], "updated_at": 1791795720.0, "voc_index": [35, 105]}}

Начало: Logs/2026-10-12/telemetry.jsonl:724; 2026-10-12T12:03:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [53, 53, 44], "online": true, "status": "OK", "temperature_c": null, "updated_at": 1791795780.0, "voc_index": [34, 104]}}

Последний кадр с признаком: Logs/2026-10-12/telemetry.jsonl:724; 2026-10-12T12:03:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [53, 53, 44], "online": true, "status": "OK", "temperature_c": null, "updated_at": 1791795780.0, "voc_index": [34, 104]}}

После: Logs/2026-10-12/telemetry.jsonl:725; 2026-10-12T12:04:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [53, 53, 45], "online": true, "status": "OK", "temperature_c": [23.01, 23.25, 21.34], "updated_at": 1791795840.0, "voc_index": [42, 105]}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
{"action": "set_lock", "actor": "agent", "kind": "action", "line": 95, "node_id": "thermal_insulation_01", "source": "Logs/2026-10-12/events.jsonl", "success": true, "team": 1, "timestamp": 1791795596.0, "value": {"lock": 0, "locked": false}}
{"action": "set_lock", "actor": "agent", "kind": "action", "line": 96, "node_id": "thermal_insulation_01", "source": "Logs/2026-10-12/events.jsonl", "success": true, "team": 1, "timestamp": 1791795604.0, "value": {"lock": 0, "locked": false}}
