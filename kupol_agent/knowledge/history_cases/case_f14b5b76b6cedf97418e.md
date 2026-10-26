# Исторический эпизод hist:f14b5b76b6cedf97418e: ACCESS_ALARM

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-13T08:51:44+03:00 — 2026-10-13T09:03:04+03:00.
Подсистемы: safety. Узлы: access_control_01.
Признак: Тревога доступа в журнале; физический осмотр и состояние двери важнее одного флага
Наблюдаемый исход: EVENT_SIGNAL_CLEARED; кадров с признаком: 1.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Признак зафиксирован событиями между минутными снимками; число событий не равно числу кадров телеметрии.

## Свидетельства
Начало: Logs/2026-10-13/events.jsonl:27; 2026-10-13T08:51:44+03:00; SHA256 380783761105f058de6d17a451aa57df8b7ee10f23f399dfc49ec4d8a0c6f865.
{"access_control_01": {"alarm": true, "event_kind": "change"}}

Последний кадр с признаком: Logs/2026-10-13/events.jsonl:27; 2026-10-13T08:51:44+03:00; SHA256 380783761105f058de6d17a451aa57df8b7ee10f23f399dfc49ec4d8a0c6f865.
{"access_control_01": {"alarm": true, "event_kind": "change"}}

После: Logs/2026-10-13/events.jsonl:40; 2026-10-13T09:03:04+03:00; SHA256 380783761105f058de6d17a451aa57df8b7ee10f23f399dfc49ec4d8a0c6f865.
{"access_control_01": {"alarm": false, "event_kind": "change"}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
{"action": "reset_alarm", "actor": "agent", "kind": "action", "line": 37, "node_id": "access_control_01", "source": "Logs/2026-10-13/events.jsonl", "success": true, "team": 1, "timestamp": 1791871380.0, "value": null}
