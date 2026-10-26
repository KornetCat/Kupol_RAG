# Исторический эпизод hist:24df87974c7b6bb4c8ef: DATA_UNAVAILABLE

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-15T19:13:00+03:00 — 2026-10-15T20:08:00+03:00.
Подсистемы: telemetry_quality, communications, power_distribution. Узлы: printer_3d_01, cnc_01.
Признак: Потеря, заморозка или пустые показания; сначала общая связь и питание
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 55.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-15/telemetry.jsonl:1153; 2026-10-15T19:12:00+03:00; SHA256 c66757c91eb454087c237355a894f1639c05ef9929077c1379453f8cf387c20d.
{"cnc_01": {"alarm": false, "alarm_code": null, "error_code": null, "hold_level": null, "hold_reason": null, "online": true, "paused": false, "power_w": 25.0, "progress_pct": 100.0, "state": "IDLE", "status": "OK", "updated_at": 1792080720.0, "z_mm": 5.0}, "printer_3d_01": {"error_code": null, "hold_level": null, "hold_reason": null, "online": true, "paused": false, "power_w": 8.0, "progress_pct": 100.0, "real_state": "COMPLETED", "speed_pct": 100.0, "state": "COMPLETED", "status": "OK", "updated_at": 1792080720.0, "webhook_state": "COMPLETED", "z_mm": 30.19}}

Начало: Logs/2026-10-15/telemetry.jsonl:1154; 2026-10-15T19:13:00+03:00; SHA256 c66757c91eb454087c237355a894f1639c05ef9929077c1379453f8cf387c20d.
{"cnc_01": {"alarm": false, "alarm_code": null, "error_code": null, "hold_level": null, "hold_reason": null, "online": false, "paused": false, "power_w": 0.0, "progress_pct": 100.0, "state": "IDLE", "status": "OK", "updated_at": 1792080780.0, "z_mm": 5.0}, "printer_3d_01": {"error_code": null, "hold_level": null, "hold_reason": null, "online": false, "paused": false, "power_w": 0.0, "progress_pct": 100.0, "real_state": "COMPLETED", "speed_pct": 100.0, "state": "COMPLETED", "status": "OK", "updated_at": 1792080780.0, "webhook_state": "COMPLETED", "z_mm": 30.19}}

Последний кадр с признаком: Logs/2026-10-15/telemetry.jsonl:1208; 2026-10-15T20:07:00+03:00; SHA256 c66757c91eb454087c237355a894f1639c05ef9929077c1379453f8cf387c20d.
{"cnc_01": {"alarm": false, "alarm_code": null, "error_code": null, "hold_level": null, "hold_reason": null, "online": false, "paused": false, "power_w": 0.0, "progress_pct": 100.0, "state": "IDLE", "status": "OK", "updated_at": 1792084020.0, "z_mm": 5.0}, "printer_3d_01": {"error_code": null, "hold_level": null, "hold_reason": null, "online": false, "paused": false, "power_w": 0.0, "progress_pct": 100.0, "real_state": "COMPLETED", "speed_pct": 100.0, "state": "COMPLETED", "status": "OK", "updated_at": 1792084020.0, "webhook_state": "COMPLETED", "z_mm": 30.19}}

После: Logs/2026-10-15/telemetry.jsonl:1209; 2026-10-15T20:08:00+03:00; SHA256 c66757c91eb454087c237355a894f1639c05ef9929077c1379453f8cf387c20d.
{"cnc_01": {"alarm": false, "alarm_code": null, "error_code": null, "hold_level": null, "hold_reason": null, "online": true, "paused": false, "power_w": 25.0, "progress_pct": 100.0, "state": "IDLE", "status": "OK", "updated_at": 1792084080.0, "z_mm": 5.0}, "printer_3d_01": {"error_code": null, "hold_level": null, "hold_reason": null, "online": true, "paused": false, "power_w": 8.0, "progress_pct": 100.0, "real_state": "COMPLETED", "speed_pct": 100.0, "state": "COMPLETED", "status": "OK", "updated_at": 1792084080.0, "webhook_state": "COMPLETED", "z_mm": 30.19}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
Связанная команда в окне ±5 минут не найдена. Причина исчезновения признака неизвестна.
