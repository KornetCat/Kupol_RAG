# Исторический эпизод hist:c74f04f8c0c9eac83de8: SENSOR_QUALITY

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-15T16:57:00+03:00 — 2026-10-15T17:08:00+03:00.
Подсистемы: telemetry_quality. Узлы: radiation_sensor_01.
Признак: Источник помечен как неисправный/деградировавший; сопоставить независимые данные
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 11.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
До: Logs/2026-10-15/telemetry.jsonl:1017; 2026-10-15T16:56:00+03:00; SHA256 c66757c91eb454087c237355a894f1639c05ef9929077c1379453f8cf387c20d.
{"radiation_sensor_01": {"alarm_state": "NORMAL", "confidence": 0.958, "dose_rate_usv_h": 0.107, "online": true, "sensor_state": "OK", "status": "OK", "updated_at": 1792072560.0}}

Начало: Logs/2026-10-15/telemetry.jsonl:1018; 2026-10-15T16:57:00+03:00; SHA256 c66757c91eb454087c237355a894f1639c05ef9929077c1379453f8cf387c20d.
{"radiation_sensor_01": {"alarm_state": "NORMAL", "confidence": 0.54, "dose_rate_usv_h": 0.047, "online": true, "sensor_state": "DEGRADED", "status": "OK", "updated_at": 1792072620.0}}

Последний кадр с признаком: Logs/2026-10-15/telemetry.jsonl:1028; 2026-10-15T17:07:00+03:00; SHA256 c66757c91eb454087c237355a894f1639c05ef9929077c1379453f8cf387c20d.
{"radiation_sensor_01": {"alarm_state": "WARNING", "confidence": 0.42, "dose_rate_usv_h": 0.2, "online": true, "sensor_state": "DEGRADED", "status": "OK", "updated_at": 1792073220.0}}

После: Logs/2026-10-15/telemetry.jsonl:1029; 2026-10-15T17:08:00+03:00; SHA256 c66757c91eb454087c237355a894f1639c05ef9929077c1379453f8cf387c20d.
{"radiation_sensor_01": {"alarm_state": "WARNING", "confidence": 0.968, "dose_rate_usv_h": 0.06, "online": true, "sensor_state": "OK", "status": "OK", "updated_at": 1792073280.0}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
Связанная команда в окне ±5 минут не найдена. Причина исчезновения признака неизвестна.
