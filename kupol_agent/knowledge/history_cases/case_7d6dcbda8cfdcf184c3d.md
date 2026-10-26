# Исторический эпизод hist:7d6dcbda8cfdcf184c3d: SMOKE_ALARM

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-16T12:10:04+03:00 — 2026-10-16T12:10:44+03:00.
Подсистемы: safety, air. Узлы: smoke_detector_01.
Признак: Краткая дымовая тревога в журнале событий; отсутствие на минутном снимке не опровергает тревогу
Наблюдаемый исход: EVENT_SIGNAL_CLEARED; кадров с признаком: 1.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Признак зафиксирован событиями между минутными снимками; число событий не равно числу кадров телеметрии.

## Свидетельства
Начало: Logs/2026-10-16/events.jsonl:183; 2026-10-16T12:10:04+03:00; SHA256 957f5a53aa8ff73e9d31168b6807ee0fac56b8ac45dcb82afcb5cfb880eabd4a.
{"smoke_detector_01": {"event_kind": "change", "smoke_detected": true}}

Последний кадр с признаком: Logs/2026-10-16/events.jsonl:183; 2026-10-16T12:10:04+03:00; SHA256 957f5a53aa8ff73e9d31168b6807ee0fac56b8ac45dcb82afcb5cfb880eabd4a.
{"smoke_detector_01": {"event_kind": "change", "smoke_detected": true}}

После: Logs/2026-10-16/events.jsonl:207; 2026-10-16T12:10:44+03:00; SHA256 957f5a53aa8ff73e9d31168b6807ee0fac56b8ac45dcb82afcb5cfb880eabd4a.
{"smoke_detector_01": {"event_kind": "change", "smoke_detected": false}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
{"action": "reset_alarm", "actor": "agent", "kind": "action", "line": 206, "node_id": "smoke_detector_01", "source": "Logs/2026-10-16/events.jsonl", "success": true, "team": 1, "timestamp": 1792141840.0, "value": null}
