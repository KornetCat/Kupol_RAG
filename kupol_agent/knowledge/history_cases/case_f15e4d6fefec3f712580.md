# Исторический эпизод hist:f15e4d6fefec3f712580: PRINTER_STATE_CONFLICT

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-14T10:51:00+03:00 — 2026-10-14T11:01:00+03:00.
Подсистемы: production, telemetry_quality, safety. Узлы: printer_3d_01, thermal_insulation_01.
Признак: Webhook завершения противоречит состоянию печати; проверять progress и координаты
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 10.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-14/telemetry.jsonl:651; 2026-10-14T10:50:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"printer_3d_01": {"error_code": null, "hold_level": null, "hold_reason": null, "online": true, "paused": false, "power_w": 130.2, "progress_pct": 31.8, "real_state": "PRINTING", "speed_pct": 100.0, "state": "PRINTING", "status": "OK", "updated_at": 1791964200.0, "webhook_state": "PRINTING", "z_mm": 9.74}, "thermal_insulation_01": {"humidity_pct": [44, 44, 47], "online": true, "status": "OK", "temperature_c": [22.19, 22.48, 19.38], "updated_at": 1791964200.0, "voc_index": [58, 188]}}

Начало: Logs/2026-10-14/telemetry.jsonl:652; 2026-10-14T10:51:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"printer_3d_01": {"error_code": null, "hold_level": null, "hold_reason": null, "online": true, "paused": false, "power_w": 122.4, "progress_pct": 32.2, "real_state": "PRINTING", "speed_pct": 100.0, "state": "PRINTING", "status": "OK", "updated_at": 1791964260.0, "webhook_state": "COMPLETED", "z_mm": 9.87}, "thermal_insulation_01": {"humidity_pct": [43, 44, 48], "online": true, "status": "OK", "temperature_c": [22.52, 22.77, 19.35], "updated_at": 1791964260.0, "voc_index": [43, 189]}}

Последний кадр с признаком: Logs/2026-10-14/telemetry.jsonl:661; 2026-10-14T11:00:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"printer_3d_01": {"error_code": null, "hold_level": null, "hold_reason": null, "online": true, "paused": false, "power_w": 119.9, "progress_pct": 36.0, "real_state": "PRINTING", "speed_pct": 100.0, "state": "PRINTING", "status": "OK", "updated_at": 1791964800.0, "webhook_state": "COMPLETED", "z_mm": 10.99}, "thermal_insulation_01": {"humidity_pct": [44, 42, 49], "online": true, "status": "OK", "temperature_c": [21.76, 22.19, 19.34], "updated_at": 1791964800.0, "voc_index": [70, 187]}}

После: Logs/2026-10-14/telemetry.jsonl:662; 2026-10-14T11:01:00+03:00; SHA256 69f4555e68c4d2c498941d682c949d9a42912cea6c5e85a23aaecf0d5435f59c.
{"printer_3d_01": {"error_code": null, "hold_level": null, "hold_reason": null, "online": true, "paused": false, "power_w": 131.4, "progress_pct": 36.4, "real_state": "PRINTING", "speed_pct": 100.0, "state": "PRINTING", "status": "OK", "updated_at": 1791964860.0, "webhook_state": "PRINTING", "z_mm": 11.12}, "thermal_insulation_01": {"humidity_pct": [47, 48, 47], "online": true, "status": "OK", "temperature_c": [21.66, 21.99, 19.42], "updated_at": 1791964860.0, "voc_index": [79, 183]}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
{"action": "set_lock", "actor": "agent", "kind": "action", "line": 84, "node_id": "thermal_insulation_01", "source": "Logs/2026-10-14/events.jsonl", "success": true, "team": 1, "timestamp": 1791964404.0, "value": {"lock": 0, "locked": false}}
{"action": "set_lock", "actor": "agent", "kind": "action", "line": 87, "node_id": "thermal_insulation_01", "source": "Logs/2026-10-14/events.jsonl", "success": true, "team": 1, "timestamp": 1791964556.0, "value": {"lock": 0, "locked": true}}
