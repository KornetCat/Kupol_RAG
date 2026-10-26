# Исторический эпизод hist:f855ccad7b553c88e29b: DATA_UNAVAILABLE

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-12T12:19:00+03:00 — 2026-10-12T12:20:00+03:00.
Подсистемы: telemetry_quality, communications, power_distribution. Узлы: thermal_insulation_01.
Признак: Потеря, заморозка или пустые показания; сначала общая связь и питание
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 1.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-12/telemetry.jsonl:739; 2026-10-12T12:18:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [35, 35, 44], "online": true, "status": "OK", "temperature_c": [22.78, 23.13, 21.45], "updated_at": 1791796680.0, "voc_index": null}}

Начало: Logs/2026-10-12/telemetry.jsonl:740; 2026-10-12T12:19:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [39, 38, 44], "online": true, "status": "OK", "temperature_c": null, "updated_at": 1791796740.0, "voc_index": [30, 104]}}

Последний кадр с признаком: Logs/2026-10-12/telemetry.jsonl:740; 2026-10-12T12:19:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": [39, 38, 44], "online": true, "status": "OK", "temperature_c": null, "updated_at": 1791796740.0, "voc_index": [30, 104]}}

После: Logs/2026-10-12/telemetry.jsonl:741; 2026-10-12T12:20:00+03:00; SHA256 34f7f54878562ab904fea4da2dfadd18a91417884bb1d35b15de3ffa8ebb129b.
{"thermal_insulation_01": {"humidity_pct": null, "online": true, "status": "OK", "temperature_c": [23.09, 23.35, 21.44], "updated_at": 1791796800.0, "voc_index": null}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
{"action": "set_lock", "actor": "agent", "kind": "action", "line": 101, "node_id": "thermal_insulation_01", "source": "Logs/2026-10-12/events.jsonl", "success": true, "team": 1, "timestamp": 1791796512.0, "value": {"lock": 0, "locked": false}}
{"action": "set_lock", "actor": "agent", "kind": "action", "line": 102, "node_id": "thermal_insulation_01", "source": "Logs/2026-10-12/events.jsonl", "success": true, "team": 1, "timestamp": 1791796520.0, "value": {"lock": 0, "locked": false}}
{"action": "set_poll_interval", "actor": "agent", "kind": "action", "line": 104, "node_id": "thermal_insulation_01", "source": "Logs/2026-10-12/events.jsonl", "success": true, "team": 1, "timestamp": 1791797100.0, "value": 500}
