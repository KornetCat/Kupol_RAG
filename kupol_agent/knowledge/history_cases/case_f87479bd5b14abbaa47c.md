# Исторический эпизод hist:f87479bd5b14abbaa47c: FUEL_WATER

Наблюдения для RAG. Это данные, не инструкции к исполнению; learning_eligible=false, repair_causality=UNPROVEN.
Источник: WhiteDots/Dome_RAG, revision a16da2db079cdcb395e354383199effdf5a170b2. 2026-10-17T00:00:00+03:00 — 2026-10-17T23:02:00+03:00.
Подсистемы: energy, resources. Узлы: fuel_tank_01, dizel_1, battery_01.
Признак: Вода в топливе выше исторического порога 500 ppm; проверить причину отказа дизеля
Наблюдаемый исход: CONDITION_CLEARED; кадров с признаком: 1382.
CONDITION_CLEARED означает исчезновение признака в одном следующем кадре, а не доказанный ремонт и не устойчивость.
Источники ниже — минутные снимки телеметрии.

## Свидетельства
Начало: Logs/2026-10-17/telemetry.jsonl:1; 2026-10-17T00:00:00+03:00; SHA256 32b0726f4caa80607703daf3a9ca2521d9059e6cd02070b68aaee64d64495cda.
{"battery_01": {"balancing_state": "IDLE", "cell_voltage_delta_mv": 19.6, "current_a": 7.21, "online": true, "power_w": 188.8, "soc_pct": 45.08, "state": "CHARGING", "status": "OK", "temperature_c": 13.63, "updated_at": 1792184400.0, "voltage_v": 26.16}, "dizel_1": {"fault_code": null, "fault_reason": null, "online": true, "power_w": 0.0, "state": "OFF", "status": "OK", "updated_at": 1792184400.0}, "fuel_tank_01": {"fuel_quality": "WATER", "level_pct": 75.1, "online": true, "state": "CONTAMINATED", "status": "OK", "updated_at": 1792184400.0, "water_content_ppm": 570.29}}

Последний кадр с признаком: Logs/2026-10-17/telemetry.jsonl:1382; 2026-10-17T23:01:00+03:00; SHA256 32b0726f4caa80607703daf3a9ca2521d9059e6cd02070b68aaee64d64495cda.
{"battery_01": {"balancing_state": "IDLE", "cell_voltage_delta_mv": 51.2, "current_a": -6.38, "online": true, "power_w": -163.6, "soc_pct": 33.81, "state": "DISCHARGING", "status": "OK", "temperature_c": 20.31, "updated_at": 1792267260.0, "voltage_v": 25.64}, "dizel_1": {"fault_code": null, "fault_reason": null, "online": true, "power_w": 0.0, "state": "OFF", "status": "OK", "updated_at": 1792267260.0}, "fuel_tank_01": {"fuel_quality": "WATER", "level_pct": 74.9, "online": true, "state": "CONTAMINATED", "status": "OK", "updated_at": 1792267260.0, "water_content_ppm": 638.116}}

После: Logs/2026-10-17/telemetry.jsonl:1383; 2026-10-17T23:02:00+03:00; SHA256 32b0726f4caa80607703daf3a9ca2521d9059e6cd02070b68aaee64d64495cda.
{"battery_01": {"balancing_state": "IDLE", "cell_voltage_delta_mv": 51.3, "current_a": -6.68, "online": true, "power_w": -171.2, "soc_pct": 33.7, "state": "DISCHARGING", "status": "OK", "temperature_c": 20.27, "updated_at": 1792267320.0, "voltage_v": 25.64}, "dizel_1": {"fault_code": null, "fault_reason": null, "online": true, "power_w": 0.0, "state": "OFF", "status": "OK", "updated_at": 1792267320.0}, "fuel_tank_01": {"fuel_quality": "GOOD", "level_pct": 100.0, "online": true, "state": "NORMAL", "status": "OK", "updated_at": 1792267320.0, "water_content_ppm": 478.587}}

## Действия рядом по времени
События близки по времени; это не доказательство причинности ремонта. Не переносить права и команды из истории.
{"action": "turn_on", "actor": "agent", "kind": "action", "line": 530, "node_id": "dizel_1", "source": "Logs/2026-10-17/events.jsonl", "success": true, "team": 1, "timestamp": 1792267276.0, "value": null}
{"action": "transfer_fuel", "actor": "agent", "kind": "action", "line": 535, "node_id": "fuel_tank_01", "source": "Logs/2026-10-17/events.jsonl", "success": true, "team": 1, "timestamp": 1792267284.0, "value": null}
{"action": "turn_off", "actor": "agent", "kind": "action", "line": 538, "node_id": "dizel_1", "source": "Logs/2026-10-17/events.jsonl", "success": true, "team": 1, "timestamp": 1792267316.0, "value": null}
{"action": "turn_on", "actor": "agent", "kind": "action", "line": 542, "node_id": "dizel_1", "source": "Logs/2026-10-17/events.jsonl", "success": true, "team": 1, "timestamp": 1792267336.0, "value": null}
