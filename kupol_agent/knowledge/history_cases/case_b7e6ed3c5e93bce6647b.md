# Исторический эпизод hist:b7e6ed3c5e93bce6647b: INTERLOCK_TRIPPED

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-15T09:36:00+03:00 — 2026-10-15T09:37:04+03:00.
Подсистемы: safety, production. Узлы: dome_automation_01.
Признак: Срабатывание автоматики; не переносить историческое квитирование или сброс в текущие полномочия
Наблюдаемый исход: EVENT_SIGNAL_CLEARED; кадров с признаком: 1.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Признак зафиксирован событиями между минутными снимками; число событий не равно числу кадров телеметрии.

## Свидетельства
Начало: Logs/2026-10-15/events.jsonl:64; 2026-10-15T09:36:00+03:00; SHA256 057faae05831530e6a88a5a897e8e79ceb2231649c1f5d1a3dacbfb74504c488.
{"dome_automation_01": {"event_kind": "change", "interlocks.IL_CONDENSATE.state": "TRIPPED"}}

Последний кадр с признаком: Logs/2026-10-15/events.jsonl:64; 2026-10-15T09:36:00+03:00; SHA256 057faae05831530e6a88a5a897e8e79ceb2231649c1f5d1a3dacbfb74504c488.
{"dome_automation_01": {"event_kind": "change", "interlocks.IL_CONDENSATE.state": "TRIPPED"}}

После: Logs/2026-10-15/events.jsonl:69; 2026-10-15T09:37:04+03:00; SHA256 057faae05831530e6a88a5a897e8e79ceb2231649c1f5d1a3dacbfb74504c488.
{"dome_automation_01": {"event_kind": "change", "interlocks.IL_CONDENSATE.state": "ARMED"}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
{"action": "set_data_trust", "actor": "agent", "kind": "action", "line": 66, "node_id": "dome_automation_01", "source": "Logs/2026-10-15/events.jsonl", "success": true, "team": 1, "timestamp": 1792046220.0, "value": {"node": "climate_sensor_01", "param": "humidity_pct", "trust": "UNRELIABLE"}}
{"action": "reset_interlock", "actor": "agent", "kind": "action", "line": 67, "node_id": "dome_automation_01", "source": "Logs/2026-10-15/events.jsonl", "success": true, "team": 1, "timestamp": 1792046220.0, "value": "IL_CONDENSATE"}
