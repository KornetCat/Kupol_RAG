"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const titleByView = { overview: "Обзор системы", telemetry: "Телеметрия", files: "Файлы и производство", history: "Журнал и опыт" };
  const nodeNames = {
    solar_panels_01: "Солнечные панели", solar_inverter_01: "Солнечный инвертор", battery_01: "Аккумуляторная батарея",
    wind_turbine_01: "Ветрогенератор", dizel_1: "Дизельный генератор", fuel_tank_01: "Топливный бак",
    smart_panel_01: "Умный щиток", printer_3d_01: "3D-принтер", cnc_01: "Фрезерный станок",
    climate_sensor_01: "Датчик климата · основной", climate_sensor_02: "Датчик климата · 02", climate_sensor_03: "Датчик климата · 03",
    air_quality_sensor_01: "Качество воздуха", smoke_detector_01: "Датчик дыма · 01", smoke_detector_02: "Датчик дыма · 02",
    fume_extraction_01: "Вытяжная вентиляция", weather_station_01: "Метеостанция", seysmo_01: "Сейсмодатчики",
    water_pump_01: "Насосная станция", water_tank_01: "Резервуар воды", water_filter_01: "Фильтрация воды",
    fire_suppression_01: "Пожаротушение", access_control_01: "Контроль доступа", radiation_sensor_01: "Датчик радиации",
    chem_sensor_01: "Химический датчик", material_inventory_01: "Склад материалов", equipment_cooling_01: "Охлаждение станков",
    supply_ventilation_01: "Приточная вентиляция", dome_sealing_01: "Герметизация Купола", radio_01: "Радиосвязь",
    backup_comms_01: "Резервная связь", edge_compute_01: "Вычислительный узел", network_link_01: "Сетевой канал",
    thermal_insulation_01: "Теплица ФабЛаб", thermal_insulation_02: "Теплица Химлаб", dome_automation_01: "Автоматика Купола", meeting_01: "Чат Купола"
  };
  const stateLabels = { ON: "Включён", OFF: "Выключен", IDLE: "Ожидает", PRINTING: "Печать", RUNNING: "Работает", PAUSED: "Пауза", ERROR: "Ошибка", ALARM: "Авария", COMPLETED: "Завершено", UNKNOWN: "Неизвестно", CHARGING: "Заряжается", DISCHARGING: "Разряжается", FULL: "Заряжена", OK: "Норма", UP: "Доступен", DOWN: "Недоступен", PENDING: "Проверяется", VERIFIED_SUCCESS: "Эффект подтверждён", VERIFIED: "Подтверждено", FAILED: "Неуспешно", BLOCKED: "Заблокировано", DRY_RUN: "Наблюдение", RESOLVED: "Устранено", OPEN: "Открыт", HUMAN_CONFIRMED_SUCCESS: "Подтверждено человеком" };
  const eventLabels = { start: "Запуск", stop: "Остановка", connection: "Соединение", observation: "Наблюдение", decision: "Решение", action: "Действие", verification: "Проверка результата", llm_call: "DeepSeek", error: "Ошибка", safety_alert: "Кризис", message: "Сообщение", upload: "Передача файла", history: "История" };
  const paramLabels = { online: "На связи", status: "Достоверность", updated_at: "Обновление (Unix)", state: "Состояние", enabled: "Включено", power_w: "Мощность, Вт", voltage_v: "Напряжение, В", current_a: "Ток, А", temperature_c: "Температура, °C", humidity_pct: "Влажность, %", co2_ppm: "CO₂, ppm", soc_pct: "Заряд, %", airflow_pct: "Поток воздуха, %", airflow_m3_h: "Поток воздуха, м³/ч", fan_rpm: "Обороты вентилятора", alarm: "Авария", alarm_code: "Код аварии", protection_state: "Защита", bus_powered: "Питание шины", bus_voltage_v: "Шина, В", total_power_w: "Суммарная мощность, Вт", line: "Номер линии", name: "Название", mode: "Режим", file_name: "Файл задания", progress_pct: "Выполнено, %", nozzle_temp_c: "Сопло, °C", bed_temp_c: "Стол, °C", spindle_temp_c: "Шпиндель, °C", coolant_flow_l_min: "Охлаждение, л/мин", lockout_remaining_s: "Осталось блокировки, с", lockout_reason: "Причина блокировки", reading_delay_s: "Задержка измерения, с", confidence: "Доверие" };

  let snapshot = { connection: { state: "disconnected" }, profile: "training", autonomous: false, telemetry: null, diagnostics: { faults: [], outcomes: [] }, catalog: {}, events: [], messages: [], credentials: {}, history: {} };
  let token = "";
  let socket = null;
  let socketRetry = null;
  let socketDelay = 1000;
  let stopped = false;
  let lastTelemetrySignature = "";
  let lastReceivedLocal = 0;
  let lastStateAt = 0;
  let streamLive = false;
  let chatPending = false;
  let connectionPending = false;
  let uploadPending = false;
  let jobStartPending = false;
  let directControlPending = false;
  let autonomyPending = false;
  let autonomyConfigPending = false;
  let autonomyConfigDirty = false;
  let autonomyConfigSignature = "";
  let autonomySessionSignature = "";
  let manualControlsSignature = "";
  let selectedRemoteFile = "";
  let localConnectionError = "";
  let clearingDirectives = false;
  let workContextPending = false;
  let workContextError = "";
  let remoteFilesDevice = "";
  let expandAll = false;
  let currentView = "overview";
  let historyData = null;
  let chatMessages = [];
  let recentEvents = [];
  const openedNodes = new Set();
  const openedEvidence = new Set();
  const evidenceScroll = new Map();
  let renderedChatSignature = "";
  let chatErrorSequence = 0;
  const number = (value) => typeof value === "number" && Number.isFinite(value);
  const formatNumber = (value, digits = 0) => number(value) ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(value) : "—";
  const plain = (value, max = 12000) => value === null || value === undefined ? "—" : typeof value === "string" ? value.slice(0, max) : JSON.stringify(value).slice(0, max);
  const labelState = (value) => stateLabels[value] || plain(value, 120);

  function el(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function empty(target, title, text, icon = "◌") {
    target.replaceChildren();
    const box = el("div", "empty-state");
    box.append(el("span", "empty-icon", icon), el("strong", "", title));
    if (text) box.append(el("p", "", text));
    target.append(box);
  }

  function badge(text, kind = "neutral") { return el("span", `badge ${kind}`, text); }

  function sourceDetails(source) {
    const record = source && typeof source === "object" ? source : { id: plain(source, 500) };
    const path = plain(record.source || record.file || "", 1200);
    const filename = path ? path.split(/[\\/]/).at(-1) : "";
    const provenance = record.provenance && typeof record.provenance === "object" ? record.provenance : {};
    const categories = { historical_episode: "Исторический эпизод", narrative: "Дневник человека", manual_experience: "Ручной опыт", runbook: "Сценарий", reference: "Документация", verified_case: "Проверенный случай", observed_case: "Память случаев", agent_case: "Память случаев", case: "Память случаев", document: "Документ" };
    const kind = record.category || provenance.type || (path.includes("history_cases") ? filename === "historical_logbook.md" ? "narrative" : "historical_episode" : path.includes("field_experience") ? "manual_experience" : filename === "runbooks.md" ? "runbook" : record.kind || "document");
    const friendly = categories[kind] || "Источник RAG";
    const title = plain(record.section || record.title || friendly, 1200);
    const key = plain(record.id || record.source_id || `${path}:${title}`, 1400);
    const details = el("details", "source-details");
    details.dataset.sourceKey = key;
    details.open = openedEvidence.has(key);
    const summary = el("summary");
    summary.append(el("span", "source-title", title), el("span", "source-category", friendly));
    const body = el("div", "source-content");
    const excerpt = record.excerpt || record.text || record.summary;
    const excerptNode = el("p", "source-excerpt", excerpt ? plain(excerpt, 1600) : "Фрагмент текста не передан. Ниже указано происхождение источника.");
    excerptNode.addEventListener("scroll", () => {
      evidenceScroll.delete(key);
      evidenceScroll.set(key, excerptNode.scrollTop);
      while (evidenceScroll.size > 256) evidenceScroll.delete(evidenceScroll.keys().next().value);
    }, { passive: true });
    body.append(excerptNode);
    const metadata = el("dl", "source-metadata");
    const row = (label, value) => { if (value !== undefined && value !== null && value !== "") metadata.append(el("dt", "", label), el("dd", "", plain(value, 1400))); };
    row("Файл", filename || (record.kind === "case" ? "Память SQLite" : "Не указан"));
    row("ID", record.id || record.source_id);
    row("Эпизод", record.episode_id || provenance.episode_id);
    row("Результат", record.outcome ? labelState(record.outcome) : null);
    row("Ревизия", provenance.source_revision || record.source_revision);
    row("Происхождение", provenance.type ? categories[provenance.type] || provenance.type : null);
    row("Среда", provenance.environment);
    row("Проверка", provenance.verification);
    row("Причинность ремонта", record.repair_causality === "UNPROVEN" ? "Не доказана" : record.repair_causality);
    const refs = provenance.evidence_sources || record.evidence_sources;
    if (Array.isArray(refs) && refs.length) row("Свидетельства", refs.slice(0, 3).map((ref) => typeof ref === "string" ? ref : `${plain(ref.file || ref.source || "Источник", 300)}${ref.line != null ? `:${ref.line}` : ""}${ref.at ? ` · ${ref.at}` : ""}`).join("\n"));
    body.append(metadata);
    details.append(summary, body);
    details.addEventListener("toggle", () => {
      if (details.open) {
        openedEvidence.delete(key);
        openedEvidence.add(key);
        while (openedEvidence.size > 256) openedEvidence.delete(openedEvidence.values().next().value);
        if (evidenceScroll.has(key)) window.requestAnimationFrame(() => { if (details.isConnected && details.open) excerptNode.scrollTop = evidenceScroll.get(key) || 0; });
      } else openedEvidence.delete(key);
    });
    return details;
  }

  function sourceGroup(sources, title, limit = 4) {
    if (!Array.isArray(sources) || !sources.length) return null;
    const group = el("section", "evidence-group");
    group.append(el("h5", "evidence-heading", title));
    sources.slice(0, limit).forEach((source) => group.append(sourceDetails(source)));
    return group;
  }

  function commandResults(commands) {
    if (!Array.isArray(commands) || !commands.length) return null;
    const group = el("section", "command-results");
    group.append(el("h5", "evidence-heading", "Фактические результаты команд"));
    const states = {
      STATE_CONFIRMED: ["Состояние подтверждено", "good"], ACKNOWLEDGED_ONLY: ["API принял · эффект не подтверждён", "warning"],
      UNKNOWN: ["Результат неизвестен", "warning"], REJECTED: ["Команда отклонена", "error"],
      SUPERSEDED: ["Заменено новым указанием", "neutral"], SUPERSEDED_AFTER_SEND: ["Заменено после отправки", "warning"],
      BLOCKED: ["Выполнение заблокировано", "warning"], PENDING: ["Ожидает проверки", "neutral"]
    };
    commands.slice(0, 4).forEach((command) => {
      if (!command || typeof command !== "object") return;
      const request = command.request && typeof command.request === "object" ? command.request : command;
      const state = states[command.status] || ["Выполнение не подтверждено", "neutral"];
      const card = el("article", `command-result ${state[1]}`);
      const top = el("div", "command-result-heading");
      top.append(el("strong", "", request.node_id ? nodeTitle(request.node_id) : "Команда Куполу"), badge(state[0], state[1]));
      card.append(top);
      let value = request.value;
      if (value && typeof value === "object" && value.file_name) value = value.file_name;
      card.append(el("p", "command-request", `${request.node_id || "—"} · ${request.action || request.type || "действие"}${value !== undefined ? ` · ${plain(value, 350)}` : ""}`));
      const error = command.response?.error || command.error;
      if (error) card.append(el("p", "command-result-error", plain(error, 1000)));
      if (command.message) card.append(el("p", "command-result-note", plain(command.message, 1000)));
      if (command.limitation) card.append(el("p", "command-result-note", command.limitation === "NO_TELEMETRY_PREDICATE" ? "Для этого действия нет подтверждающего параметра телеметрии." : `Ограничение проверки: ${plain(command.limitation, 500)}`));
      if (command.status === "STATE_CONFIRMED" && command.stable_repair_verified !== true) card.append(el("p", "command-result-note", "Подтверждено новое состояние. Устойчивое устранение неисправности отдельно не подтверждено."));
      else if (command.status === "ACKNOWLEDGED_ONLY") card.append(el("p", "command-result-note", "Принятие команды API не доказывает её фактическое выполнение."));
      else if (command.status === "UNKNOWN" || command.status === "SUPERSEDED_AFTER_SEND") card.append(el("p", "command-result-note", "Команда могла быть исполнена. Её результат требует сверки; автоматического повтора нет."));
      else if (command.status === "SUPERSEDED") card.append(el("p", "command-result-note", "Этот шаг заменён более новым указанием до отправки."));
      group.append(card);
    });
    if (commands.length > 4) group.append(el("p", "command-result-note", `Ещё результатов: ${commands.length - 4}. Подробности — в журнале.`));
    return group;
  }

  function toast(message, kind = "") {
    const node = el("div", `toast ${kind}`, plain(message, 1200));
    $("toastRegion").append(node);
    window.setTimeout(() => node.remove(), 7500);
  }

  async function api(path, { method = "GET", body, timeout = 60000 } = {}) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    const headers = { Accept: "application/json" };
    if (method !== "GET") {
      if (!token) throw new Error("Нет сессии веб-интерфейса. Обновите страницу.");
      headers["X-Cupol-Token"] = token;
    }
    if (body !== undefined && !(body instanceof FormData)) headers["Content-Type"] = "application/json";
    try {
      const response = await fetch(path, { method, headers, body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin", cache: "no-store", signal: controller.signal });
      let result;
      try { result = await response.json(); } catch (_) { throw new Error(`Сервер интерфейса вернул некорректный ответ (${response.status}).`); }
      if (!response.ok || result?.ok === false || result?.error) throw new Error(plain(result?.error || result?.message || `Ошибка ${response.status}`, 1500));
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("Сервер не ответил за отведённое время. Проверьте журнал перед повтором команды.");
      if (error instanceof TypeError) throw new Error("Нет связи с локальным сервером интерфейса.");
      throw error;
    } finally { window.clearTimeout(timer); }
  }

  function telemetryNodes() { return snapshot.telemetry?.data?.nodes || snapshot.telemetry?.nodes || {}; }
  function telemetryStamp() { return snapshot.telemetry?.timestamp ?? snapshot.telemetry?.frame?.timestamp; }
  function isConnected() { return ["connected", "active", "authenticated"].includes(snapshot.connection?.state) || snapshot.connection?.state === "waiting" && snapshot.connection?.role === "guest"; }
  function catalogueNode(id) {
    const catalog = snapshot.catalog || {};
    if (Array.isArray(catalog)) return catalog.find((node) => node?.node_id === id) || {};
    if (Array.isArray(catalog.nodes)) return catalog.nodes.find((node) => node?.node_id === id) || {};
    return catalog[id] || {};
  }
  function nodeTitle(id) { return catalogueNode(id).title || nodeNames[id] || id; }

  const directValueActions = new Set(["line_on", "line_off", "start_job", "delete_file", "set_speed", "set_power_limit", "limit_load", "set_frequency", "set_mode", "set_fan", "set_protocol", "switch_channel", "set_traffic_priority"]);
  const directActionNames = { line_on: "Включить линию", line_off: "Выключить линию", turn_on: "Включить", turn_off: "Выключить", home: "Найти исходную позицию", resume: "Возобновить", start_job: "Запустить файл", delete_file: "Удалить файл", set_speed: "Задать скорость", set_mode: "Задать режим", set_fan: "Режим вентилятора", switch_to_reserve: "Резервный фильтр", preheat: "Предпусковой прогрев", set_mode_hybrid: "Гибридный режим", set_mode_grid_only: "Режим сети", sync_time: "Синхронизировать время" };

  function manualControls() {
    const controls = snapshot.manual_controls;
    return controls && typeof controls === "object" && !Array.isArray(controls) ? controls : {};
  }

  function directActions(nodeId) {
    const values = manualControls()[nodeId]?.controls;
    return Array.isArray(values) ? values.filter((value) => typeof value === "string" && value !== "upload_file") : [];
  }

  function refreshDirectActions() {
    const action = $("directControlAction");
    const previous = action.value;
    const values = directActions($("directControlNode").value);
    action.replaceChildren(...values.map((value) => {
      const option = el("option", "", `${directActionNames[value] || value} · ${value}`);
      option.value = value;
      return option;
    }));
    if (values.includes(previous)) action.value = previous;
    if (!values.length) action.append(new Option("Нет доступных действий", ""));
    if (action.value !== previous) $("directControlValue").value = "";
  }

  function renderDirectControls() {
    const controls = manualControls();
    const signature = JSON.stringify(controls);
    if (signature !== manualControlsSignature) {
      manualControlsSignature = signature;
      const select = $("directControlNode");
      const previous = select.value;
      const ids = Object.keys(controls).filter((id) => directActions(id).length).sort();
      select.replaceChildren(...ids.map((id) => {
        const option = el("option", "", `${controls[id].title || nodeTitle(id)} · ${id}`);
        option.value = id;
        return option;
      }));
      if (ids.includes(previous)) select.value = previous;
      else if (ids.includes("smart_panel_01")) select.value = "smart_panel_01";
      if (!ids.length) select.append(new Option("Нет доступных узлов", ""));
      refreshDirectActions();
      if (select.value !== previous) $("directControlValue").value = "";
    }
    const nodeId = $("directControlNode").value;
    const action = $("directControlAction").value;
    const age = telemetryAge();
    const busy = directControlPending || chatPending || jobStartPending || uploadPending || autonomyPending || autonomyConfigPending || snapshot.directives?.operation_pending === true;
    let reason = "";
    if (!isConnected() || snapshot.connection?.role !== "team") reason = "Подключитесь с правами команды.";
    else if (snapshot.safety?.hazard_latched || snapshot.safety?.emergency_stop === true) reason = "Управление заблокировано аварийным состоянием.";
    else if (age === null || age > 15 || !number(telemetryNodes()[nodeId]?.updated_at) || !nodeQuality(telemetryNodes()[nodeId]).usable) reason = "Нужна свежая достоверная телеметрия выбранного узла.";
    else if (!nodeId || !directActions(nodeId).includes(action)) reason = "Выберите доступный узел и действие из текущего каталога.";
    else if (busy) reason = directControlPending ? "Команда отправляется, ожидается ответ и проверка нового состояния…" : "Дождитесь завершения текущей ручной операции.";
    const ready = !reason;
    const selectable = isConnected() && snapshot.connection?.role === "team" && !busy && !snapshot.safety?.hazard_latched && snapshot.safety?.emergency_stop !== true;
    $("directControlNode").disabled = !selectable || !Object.keys(controls).length;
    $("directControlAction").disabled = !selectable || !directActions(nodeId).length;
    $("directControlValue").disabled = !ready || !directValueActions.has(action);
    $("directControlSend").disabled = !ready;
    $("directControlSend").textContent = directControlPending ? "Проверка результата…" : "Отправить команду";
    $("directControlAvailability").textContent = reason || "Команда выполняется один раз и не создаёт указание на будущее.";
    const hints = { line_on: "Номер линии 1–8, например 7.", line_off: "Номер линии 1–8, например 7.", start_job: "Имя существующего файла из каталога устройства.", delete_file: "Имя файла из каталога устройства.", set_speed: "Число процентов; допустимый диапазон проверяет сервер.", set_mode: "RECIRCULATION, FRESH_AIR или MIXED.", set_fan: "AUTO, ON или OFF.", switch_channel: "SATELLITE, LORA или MESH.", set_traffic_priority: "NORMAL, TELEMETRY или EMERGENCY." };
    $("directControlValueHint").textContent = directValueActions.has(action) ? hints[action] || "Число, строка или JSON-объект согласно API выбранного действия." : "Для выбранного действия параметр value не передаётся.";
  }

  function parseDirectValue(raw) {
    if (!raw.trim()) throw new Error("Введите значение для выбранного действия.");
    try { return JSON.parse(raw); }
    catch (_) {
      if (/^[^\u0000-\u001f{}\[\]"]{1,180}$/.test(raw.trim())) return raw.trim();
      throw new Error("Значение должно быть корректным JSON или простой строкой без управляющих символов.");
    }
  }

  function renderAutonomySetup() {
    const active = Boolean(snapshot.autonomous);
    const busy = autonomyPending || autonomyConfigPending;
    const reason = snapshot.safety?.autonomy_reason || snapshot.safety?.reason || (!active && autonomyConfigDirty ? "Сохраните и подтвердите изменённые параметры перед включением автоуправления." : "");
    $("autonomyToggle").checked = active;
    $("autonomyLabel").textContent = autonomyPending ? "Применение…" : active ? "Включено" : "Выключено";
    $("autonomyDescription").textContent = reason || (active ? "Разрешённые автоматические сценарии текущей сессии. Результат проверяется по новой телеметрии." : snapshot.safety?.autonomy_ready === true ? "Готово к запуску разрешённых автоматических сценариев текущей сессии." : "Наблюдение и диагностика без автоматических команд");
    $("autonomyToggle").disabled = !token || busy || (!active && autonomyConfigDirty);
    renderAutonomyConfiguration();
  }

  const autonomyFields = { min_airflow_pct: "minAirflowPct", min_supply_flow_m3_h: "minSupplyFlow", min_cooling_flow_l_min: "minCoolingFlow" };

  function autonomyCriteria() {
    return Object.fromEntries(Object.entries(autonomyFields).map(([field, id]) => [field, $(id).value === "" ? NaN : Number($(id).value)]));
  }

  function renderAutonomyConfiguration() {
    const connection = snapshot.connection || {};
    const session = JSON.stringify([connection.session_id, connection.state, connection.address, connection.team, snapshot.profile]);
    if (session !== autonomySessionSignature) {
      autonomySessionSignature = session;
      $("autonomySafetyConfirmed").checked = false;
      $("autonomyConfigStatus").textContent = "";
    }
    const settings = snapshot.autonomy_settings || {};
    const saved = Object.fromEntries(Object.keys(autonomyFields).map((key) => [key, settings[key] ?? null]));
    const signature = JSON.stringify(saved);
    if (!autonomyConfigDirty && signature !== autonomyConfigSignature) {
      autonomyConfigSignature = signature;
      Object.entries(autonomyFields).forEach(([key, id]) => { $(id).value = number(saved[key]) && saved[key] > 0 ? String(saved[key]) : ""; });
    }
    const nodes = telemetryNodes();
    const age = telemetryAge();
    const measurement = (nodeId, field, unit) => {
      const node = nodes[nodeId];
      return age !== null && age <= 15 && number(node?.updated_at) && nodeQuality(node).usable && number(node[field]) ? `${formatNumber(node[field], 2)} ${unit}` : "нет свежего достоверного показания";
    };
    $("currentFumeFlow").textContent = `Сейчас: ${measurement("fume_extraction_01", "airflow_pct", "%")}.`;
    $("currentSupplyFlow").textContent = `Сейчас общий: ${measurement("supply_ventilation_01", "airflow_m3_h", "м³/ч")}; свежий: ${measurement("supply_ventilation_01", "fresh_air_m3_h", "м³/ч")}.`;
    $("currentCoolingFlow").textContent = `Сейчас: ${measurement("equipment_cooling_01", "coolant_flow_l_min", "л/мин")}.`;
    const values = autonomyCriteria();
    const valid = Object.values(values).every((value) => number(value) && value > 0) && values.min_airflow_pct <= 100;
    let reason = "";
    if (connection.state !== "connected" || connection.role !== "team" || String(connection.team) !== "4") reason = "Для подтверждения подключитесь с правами команды 4.";
    else if (age === null || age > 15) reason = "Для подтверждения нужна свежая телеметрия.";
    else if (snapshot.safety?.hazard_latched || snapshot.safety?.emergency_stop === true) reason = "Аварийное состояние запрещает подтверждение автоуправления.";
    else if (snapshot.autonomous) reason = "Перед изменением параметров выключите автоматическое реагирование.";
    const busy = autonomyConfigPending || autonomyPending || connectionPending || snapshot.directives?.operation_pending === true;
    Object.values(autonomyFields).forEach((id) => { $(id).disabled = busy; });
    $("autonomySafetyConfirmed").disabled = Boolean(reason) || busy;
    $("saveAutonomyConfig").disabled = !token || Boolean(reason) || busy || !valid || !$("autonomySafetyConfirmed").checked;
    $("saveAutonomyConfig").textContent = autonomyConfigPending ? "Сохранение…" : "Сохранить и подтвердить";
    $("autonomyConfigAvailability").textContent = reason || (autonomyConfigPending ? "Настройки сохраняются; автоуправление не включается." : !valid ? "Укажите положительные минимальные потоки во всех трёх полях; вытяжка — не выше 100 %." : !$("autonomySafetyConfirmed").checked ? settings.confirmed && !autonomyConfigDirty ? "Параметры подтверждены для текущей сессии. Новое сохранение требует нового подтверждения." : "Проверьте параметры и явно подтвердите безопасное состояние подключённого объекта." : "Сохранение не включает автоуправление. После него используйте отдельный переключатель.");
  }

  function nodeQuality(node) {
    if (!node || typeof node !== "object") return { label: "Нет данных", kind: "neutral", usable: false };
    if (node.online === false) return { label: "Не в сети", kind: "error", usable: false };
    if (node.online !== true || node.status !== "OK") return { label: "Данные не подтверждены", kind: "warning", usable: false };
    const stamp = telemetryStamp();
    if (number(stamp) && number(node.updated_at)) {
      const age = stamp - node.updated_at;
      if (age < -0.001 || age > 15) return { label: "Устаревшие данные", kind: "warning", usable: false };
    }
    return { label: "В сети", kind: "good", usable: true };
  }

  function telemetryAge() {
    const raw = snapshot.telemetry?.received_at;
    if (typeof raw === "string") {
      const stamp = Date.parse(raw);
      if (Number.isFinite(stamp)) return Math.max(0, (Date.now() - stamp) / 1000);
    }
    if (number(raw)) return Math.max(0, Date.now() / 1000 - (raw > 100000000000 ? raw / 1000 : raw));
    if (number(snapshot.telemetry?.age_s)) return Math.max(0, snapshot.telemetry.age_s + (Date.now() - lastStateAt) / 1000);
    return lastReceivedLocal ? Math.max(0, (Date.now() - lastReceivedLocal) / 1000) : null;
  }

  function renderFreshness() {
    const hasTelemetry = Object.keys(telemetryNodes()).length > 0;
    const age = telemetryAge();
    const fresh = hasTelemetry && age !== null && age <= 8 && isConnected() && snapshot.connection?.role === "team";
    const node = $("freshnessLabel");
    node.className = `freshness ${fresh ? "fresh" : hasTelemetry ? "stale" : ""}`;
    node.replaceChildren(el("span", "status-dot"), document.createTextNode(!hasTelemetry ? "Ожидание телеметрии" : fresh ? `Обновлено ${formatNumber(age, 0)} с назад` : `Данные ${age === null ? "без отметки времени" : `${formatNumber(age, 0)} с назад`}`));
    $("chatContextDot").style.background = fresh ? "var(--teal)" : hasTelemetry ? "var(--amber)" : "var(--subtle)";
    $("chatContextLabel").textContent = snapshot.connection?.role === "guest" ? "Гостевой доступ · только разрешённые данные" : !hasTelemetry ? "Ожидает данные Купола" : fresh ? "В контексте — текущая телеметрия" : "Последние данные · свежесть не подтверждена";
    $("footerStatus").textContent = hasTelemetry ? `${Object.keys(telemetryNodes()).length} узлов · ${fresh ? "живой поток" : "сохранённый снимок"}` : "Данные появятся после подключения";
    renderDirectControls();
    renderAutonomyConfiguration();
  }

  function renderConnection() {
    const connection = snapshot.connection || {};
    const connected = isConnected();
    const connecting = ["connecting", "reconnecting", "authorizing"].includes(connection.state);
    const states = { connected: ["Подключён", "good"], active: ["Подключён", "good"], authenticated: ["Подключён", "good"], connecting: ["Подключение…", "warning"], reconnecting: ["Переподключение…", "warning"], authorizing: ["Авторизация…", "warning"], disconnected: ["Не подключён", "neutral"], disconnecting: ["Отключение…", "warning"], error: ["Ошибка соединения", "error"], guest: ["Гостевая сессия", "warning"] };
    const current = states[connection.state] || [connection.state || "Не подключён", "neutral"];
    $("connectionBadge").textContent = connection.role === "guest" && connected ? "Гостевая сессия" : current[0];
    $("connectionBadge").className = `badge ${connection.role === "guest" && connected ? "warning" : current[1]}`;
    $("connectButton").disabled = connectionPending || connected || connecting || !token;
    $("connectButton").textContent = connectionPending || connecting ? "Подключение…" : "Подключить";
    $("disconnectButton").disabled = connectionPending || !(connected || connecting || connection.state === "guest");
    $("domeAddress").disabled = connected || connecting || connectionPending;
    $("profileSelect").disabled = connected || connecting || connectionPending;
    if (connection.address && (connected || connecting) && document.activeElement !== $("domeAddress")) $("domeAddress").value = connection.address;
    if (snapshot.profile && (connected || connecting) && document.activeElement !== $("profileSelect")) $("profileSelect").value = snapshot.profile;
    const details = connected ? `${connection.role === "team" ? "Активная команда" : connection.role === "guest" ? "Гостевой доступ" : "Сессия"}${connection.team != null ? ` · команда ${connection.team}` : ""} · ${connection.address || "адрес не указан"}` : connecting ? "Сервер проверяет подключение и права доступа." : "Адрес не сохраняется между запусками интерфейса.";
    $("connectionDetails").textContent = details;
    $("sidebarTeam").textContent = connection.team != null ? String(connection.team).padStart(2, "0") : "04";
    $("sidebarRole").textContent = connected ? connection.role === "team" ? "Активная" : "Гость" : "Нет сессии";
    const error = connection.last_error || localConnectionError;
    $("connectionError").hidden = !error;
    $("connectionError").textContent = error;
    $("profileNotice").hidden = (connected ? snapshot.profile : $("profileSelect").value) !== "competition";
    renderAutonomySetup();
    const deepseekReady = Boolean(snapshot.credentials?.deepseek || snapshot.credentials?.deepseek_configured || snapshot.deepseek_configured);
    $("llmBadge").textContent = deepseekReady ? "Ключ задан" : "Нет ключа";
    $("llmBadge").className = `badge ${deepseekReady ? "good" : "neutral"}`;
    const modelPolicy = snapshot.model_policy || {};
    $("modelPolicyNotice").hidden = modelPolicy.ready !== false;
    $("modelPolicyReason").textContent = plain(modelPolicy.reason || modelPolicy.message || modelPolicy.reasons || "Проверьте допустимую модель в конфигурации агента.", 1600);
    $("chatModel").textContent = `${typeof snapshot.model === "string" ? snapshot.model : "DeepSeek"} · RAG`;
    $("chatSendButton").disabled = chatPending || !token;
    $("chatHint").textContent = snapshot.profile === "competition" ? "Команды проходят проверку прав и состояния оборудования" : "Enter — отправить · Shift + Enter — новая строка";
    renderUploadAvailability();
    renderDirectives();
  }

  function renderDirectives() {
    const directives = snapshot.directives || {};
    const items = Array.isArray(directives.items) ? directives.items : [];
    const pending = directives.pending === true && directives.pending_kind === "directive";
    const activeCount = items.length;
    const workDeclared = snapshot.work_context?.declared === true;
    const active = activeCount > 0 || pending || workDeclared;
    $("manualPriorityCount").textContent = pending ? "Сохраняется…" : activeCount ? `Активных: ${activeCount}` : workDeclared ? "Задано по работам" : "Нет активных";
    $("manualPriorityCount").className = `badge ${pending ? "warning" : active ? "good" : "neutral"}`;
    $("manualPrioritySummary").textContent = pending ? "Указание на будущее обрабатывается. Команды из него сейчас не выполняются." : active ? "Сохранённые указания имеют приоритет над конфликтующими автоматическими решениями до явного снятия." : "Сохранённые указания учитываются при последующих решениях агента до явного снятия.";
    const details = $("manualPriorityDetails");
    details.replaceChildren();
    items.slice(-3).forEach((item) => {
      const text = typeof item === "string" ? item : item.text || item.message || item.content || "";
      if (!text) return;
      const row = el("p", "manual-directive", plain(text, 280));
      row.title = plain(text, 1600);
      details.append(row);
    });
    details.hidden = !details.childElementCount;
    $("clearDirectivesButton").hidden = !active;
    $("clearDirectivesButton").disabled = clearingDirectives || workContextPending || !token;
    $("clearDirectivesButton").textContent = clearingDirectives ? "Снимаем указания…" : "Снять указания";
    $("requiredFumeContext").disabled = !token || workContextPending || clearingDirectives;
    if (!workContextPending) $("requiredFumeContext").checked = workDeclared && snapshot.work_context.required_fume === true;
    $("workContextStatus").textContent = workContextPending ? "Сохраняем указание…" : workContextError || (!workDeclared ? "Потребность в вытяжке не указана." : snapshot.work_context.required_fume === true ? "Задано: текущим работам требуется вытяжка." : "Задано: текущим работам вытяжка не требуется.");
    $("workContextStatus").className = workContextError ? "work-context-error" : "";
  }

  function metric(id, value, unit, digits = 0) {
    $(id).replaceChildren(document.createTextNode(formatNumber(value, digits)), el("small", "", unit));
  }

  function renderMetrics() {
    const nodes = telemetryNodes();
    const battery = nodes.battery_01 || {};
    const inverter = nodes.solar_inverter_01 || {};
    const climate = nodes.climate_sensor_01 || {};
    const panel = nodes.smart_panel_01 || {};
    const soc = battery.soc_pct ?? inverter.battery_soc_pct;
    metric("metricBattery", soc, "%", 1);
    metric("metricCo2", climate.co2_ppm, "ppm");
    metric("metricTemperature", climate.temperature_c, "°C", 1);
    metric("metricPower", panel.total_power_w ?? inverter.load_power_w, "Вт", 1);
    $("batteryMeter").style.width = number(soc) ? `${Math.min(100, Math.max(0, soc))}%` : "0%";
    $("batteryMeter").style.background = number(soc) && soc < 25 ? "var(--amber)" : "var(--teal)";
    $("noteBattery").textContent = battery.state ? labelState(battery.state) : nodeQuality(nodes.battery_01).label;
    $("noteBattery").className = `metric-note ${number(soc) && soc < 25 ? "warning" : ""}`;
    $("noteCo2").textContent = climate.co2_ppm == null ? "Нет данных" : nodeQuality(climate).usable ? "Основной датчик · данные доступны" : nodeQuality(climate).label;
    $("noteCo2").className = `metric-note ${number(climate.co2_ppm) && climate.co2_ppm > 2000 ? "warning" : ""}`;
    $("noteTemperature").textContent = climate.humidity_pct == null ? "Нет данных о влажности" : `Влажность ${formatNumber(climate.humidity_pct, 1)} %`;
    $("notePower").textContent = panel.bus_powered === true ? `Шина ${formatNumber(panel.bus_voltage_v, 1)} В` : panel.bus_powered === false ? "Питание шины отсутствует" : "Нет данных о питании";
    renderMachines($("machineOverview"));
    renderMachines($("filesMachines"));
  }

  function renderMachines(target) {
    target.replaceChildren();
    const nodes = telemetryNodes();
    for (const id of ["printer_3d_01", "cnc_01"]) {
      const node = nodes[id] || {};
      const isPrinter = id === "printer_3d_01";
      const card = el("article", "machine-card");
      const top = el("div", "machine-top");
      top.append(el("div", "machine-icon", isPrinter ? "▱" : "⊞"));
      const heading = el("div", "machine-heading");
      heading.append(el("h4", "", isPrinter ? "3D-принтер" : "Фрезерный станок"), el("p", "", id));
      const kind = ["PRINTING", "RUNNING"].includes(node.state) ? "running" : ["PAUSED", "HELD"].includes(node.state) ? "warning" : ["ERROR", "ALARM"].includes(node.state) ? "error" : "";
      top.append(heading, el("span", `machine-state ${kind}`, node.state ? labelState(node.state) : "Нет данных"));
      card.append(top);
      const values = el("div", "machine-values");
      const first = el("span", "", `${formatNumber(isPrinter ? node.nozzle_temp_c : node.spindle_temp_c, 1)} °C`);
      first.append(el("small", "", isPrinter ? "Температура сопла" : "Температура шпинделя"));
      const second = el("span", "", `${formatNumber(node.progress_pct, 1)} %`);
      second.append(el("small", "", "Выполнение задания"));
      values.append(first, second);
      card.append(values, el("p", "machine-file", node.file_name ? `Файл: ${node.file_name}` : "Активное задание не указано"));
      const progress = el("div", "progress-line");
      const fill = el("span");
      fill.style.width = number(node.progress_pct) ? `${Math.max(0, Math.min(100, node.progress_pct))}%` : "0%";
      progress.append(fill);
      card.append(progress);
      if (node.hold_reason || node.error_text) card.append(el("p", "machine-file", plain(node.hold_reason || node.error_text, 500)));
      target.append(card);
    }
  }

  function renderDiagnostics() {
    const faults = Array.isArray(snapshot.diagnostics?.faults) ? snapshot.diagnostics.faults : [];
    $("faultCount").textContent = `${faults.length} ${faults.length === 1 ? "событие" : faults.length > 1 && faults.length < 5 ? "события" : "событий"}`;
    const target = $("diagnosticsList");
    if (!faults.length) empty(target, Object.keys(telemetryNodes()).length ? "Активные неисправности не обнаружены" : "Диагностика ожидает телеметрию", Object.keys(telemetryNodes()).length ? "Агент продолжает отслеживать общие причины, достоверность данных и состояние устройств." : "Подключите Купол, чтобы получить состояние и первые диагностические выводы.", "◇");
    else {
      target.replaceChildren();
      faults.slice(0, 30).forEach((fault) => {
        const item = el("article", "diagnostic-item");
        const top = el("div", "diagnostic-top");
        top.append(el("span", `severity-dot ${fault.severity === "critical" ? "critical" : ""}`));
        const body = el("div", "diagnostic-body");
        body.append(el("h4", "", plain(fault.message || fault.diagnosis || fault.title || fault.code, 1200)), el("div", "diagnostic-code", `${fault.code || "DIAGNOSIS"}${fault.target ? ` · ${fault.target}` : ""}`));
        const explanation = fault.diagnosis || fault.cause || fault.reason;
        if (explanation && explanation !== fault.message) body.append(el("p", "", plain(explanation, 2000)));
        const planner = fault.planner && typeof fault.planner === "object" ? fault.planner : {};
        if (planner.diagnosis) {
          const answer = el("div", "planner-answer");
          answer.append(el("strong", "", "Вывод DeepSeek"), el("p", "", plain(planner.diagnosis, 2200)));
          body.append(answer);
        } else if (planner.error) body.append(el("p", "planner-unavailable", `DeepSeek: ${plain(planner.error, 300)}. Отображается диагностика правил.`));
        if (Array.isArray(planner.checks) && planner.checks.length) {
          const checks = el("ul", "planner-checks");
          planner.checks.slice(0, 4).forEach((check) => checks.append(el("li", "", plain(check, 500))));
          body.append(el("h5", "evidence-heading", "Предложенные проверки"), checks);
        }
        const meta = el("div", "diagnostic-meta");
        if (fault.selected_runbook) meta.append(el("span", "", `Сценарий: ${fault.selected_runbook}`));
        if (fault.gate?.reason || fault.gate_reason) meta.append(el("span", "", plain(fault.gate?.reason || fault.gate_reason, 300)));
        if (Array.isArray(fault.subsystems)) fault.subsystems.slice(0, 4).forEach((system) => meta.append(el("span", "", system)));
        if (meta.childElementCount) body.append(meta);
        const retrieved = Array.isArray(fault.retrieved) ? fault.retrieved.slice(0, 4) : [];
        const usedIds = new Set(Array.isArray(planner.evidence_ids) ? planner.evidence_ids : []);
        const cited = retrieved.filter((source) => source && usedIds.has(source.id));
        const found = retrieved.filter((source) => !source || !usedIds.has(source.id));
        const citedGroup = sourceGroup(cited, "Источники вывода DeepSeek");
        const foundGroup = sourceGroup(found, "Найденные источники");
        if (citedGroup) body.append(citedGroup);
        if (foundGroup) body.append(foundGroup);
        top.append(body);
        item.append(top);
        target.append(item);
      });
    }
    const advisory = snapshot.diagnostics?.advisory;
    const advisoryText = typeof advisory === "string" ? advisory : advisory?.answer || advisory?.content || advisory?.text || advisory?.diagnosis;
    $("deepseekAdvisoryPanel").hidden = !advisoryText;
    $("deepseekAdvisoryBody").replaceChildren();
    if (advisoryText) {
      $("deepseekAdvisoryBody").append(el("p", "advisory-answer", plain(advisoryText, 12000)));
      const evidence = sourceGroup(advisory?.sources, "Источники ответа");
      if (evidence) $("deepseekAdvisoryBody").append(evidence);
    }
    const critical = faults.filter((fault) => fault.severity === "critical");
    const safety = snapshot.safety || {};
    const emergency = safety.emergency || safety.emergency_stop || safety.hazard_latched;
    $("criticalBanner").hidden = !critical.length && !emergency;
    $("criticalTitle").textContent = emergency ? "Активно ограничение безопасности" : critical.length === 1 ? "Обнаружена кризисная ситуация" : `Критических событий: ${critical.length}`;
    $("criticalText").textContent = safety.reason || safety.message || critical.slice(0, 2).map((fault) => fault.message || fault.code).join(" · ");
  }

  function renderEvents() {
    const incoming = Array.isArray(snapshot.events) ? snapshot.events : [];
    if (incoming.length) recentEvents = incoming.slice(-200);
    renderEventList($("recentEvents"), recentEvents.slice(-6).reverse());
    renderEventList($("allEvents"), recentEvents.slice().reverse());
    $("eventCount").textContent = String(recentEvents.length);
  }

  function renderEventList(target, events) {
    if (!events.length) { empty(target, "Журнал пока пуст", "Здесь появятся этапы подключения, диагностические решения и результаты действий.", "◷"); return; }
    target.replaceChildren();
    events.forEach((event) => {
      const row = el("article", "event-row");
      const date = new Date(event.ts || event.at || event.timestamp || "");
      const stamp = Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date) : "—";
      row.append(el("time", "event-time", stamp), el("span", `event-dot ${/^[a-z_]+$/.test(event.kind) ? event.kind : ""}`));
      const message = el("div", "event-message", plain(event.message || event.summary || event.situation || event.error || event.details || event.event || event.kind, 1500));
      message.append(el("div", "event-kind", `${eventLabels[event.kind] || event.kind || "Событие"}${event.seq != null ? ` · #${event.seq}` : ""}`));
      row.append(message);
      target.append(row);
    });
  }

  function flatten(value, prefix = "", output = [], depth = 0) {
    if (output.length >= 1200 || depth > 12) return output;
    if (value && typeof value === "object") {
      const entries = Object.entries(value);
      if (!entries.length) output.push([prefix || "—", Array.isArray(value) ? "[]" : "{}"]);
      entries.forEach(([key, child]) => flatten(child, Array.isArray(value) ? `${prefix}[${key}]` : prefix ? `${prefix}.${key}` : key, output, depth + 1));
    } else output.push([prefix || "—", typeof value === "boolean" ? value ? "да" : "нет" : value == null ? "нет данных" : plain(value, 1000)]);
    return output;
  }

  function renderNodes() {
    const nodes = telemetryNodes();
    const entries = Object.entries(nodes);
    const online = entries.filter(([, node]) => node?.online === true).length;
    $("navNodeCount").textContent = String(entries.length);
    $("nodeSummary").textContent = entries.length ? `${online} из ${entries.length} на связи` : "Нет данных";
    const query = $("nodeSearch").value.toLocaleLowerCase("ru").trim();
    const filter = $("nodeFilter").value;
    const filtered = entries.filter(([id, node]) => {
      if (filter === "online" && node?.online !== true) return false;
      if (filter === "problem" && nodeQuality(node).usable) return false;
      return !query || `${id} ${nodeTitle(id)} ${JSON.stringify(node)}`.toLocaleLowerCase("ru").includes(query);
    });
    const target = $("nodesGrid");
    if (!filtered.length) { empty(target, entries.length ? "Подходящие узлы не найдены" : "Ожидание узлов Купола", entries.length ? "Измените поисковую строку или фильтр." : "Параметры появятся после подключения к серверу.", "▥"); return; }
    const scrollPositions = new Map(Array.from(target.querySelectorAll("details[data-node]"), (node) => [node.dataset.node, node.querySelector(".node-params")?.scrollTop || 0]));
    target.replaceChildren();
    filtered.forEach(([id, node]) => {
      const quality = nodeQuality(node);
      const card = el("details", "node-card");
      card.dataset.node = id;
      card.open = expandAll || openedNodes.has(id);
      const summary = el("summary");
      summary.append(el("span", `status-dot ${quality.usable ? "good" : ""}`));
      summary.firstChild.style.background = quality.kind === "good" ? "var(--teal)" : quality.kind === "error" ? "var(--red)" : "var(--amber)";
      const name = el("span", "node-label");
      name.append(el("strong", "", nodeTitle(id)), el("small", "", id));
      summary.append(name, badge(quality.label, quality.kind));
      card.append(summary);
      const params = flatten(node);
      const quick = el("div", "node-quick");
      const preferred = ["state", "co2_ppm", "temperature_c", "soc_pct", "power_w", "enabled", "status"];
      preferred.filter((key) => node?.[key] !== undefined).slice(0, 3).forEach((key) => {
        const field = el("div", "", `${paramLabels[key] || key}: `);
        field.append(el("span", "", typeof node[key] === "boolean" ? node[key] ? "да" : "нет" : labelState(node[key])));
        quick.append(field);
      });
      if (!quick.childElementCount) quick.append(el("div", "", `${params.length} параметров`));
      card.append(quick);
      const scroll = el("div", "node-params");
      const table = el("table");
      table.setAttribute("aria-label", `Параметры: ${nodeTitle(id)}`);
      const tbody = el("tbody");
      params.forEach(([key, value]) => {
        const row = el("tr");
        const label = el("th", "", key);
        label.scope = "row";
        if (paramLabels[key]) label.title = paramLabels[key];
        row.append(label, el("td", "", value));
        tbody.append(row);
      });
      table.append(tbody);
      scroll.append(table);
      card.append(scroll);
      card.addEventListener("toggle", () => { if (card.open) openedNodes.add(id); else openedNodes.delete(id); });
      target.append(card);
      scroll.scrollTop = scrollPositions.get(id) || 0;
    });
  }

  function normalizeMessage(message) {
    if (typeof message === "string") return { role: "assistant", content: message };
    return { ...message, role: message?.role || (message?.direction === "out" ? "user" : "assistant"), content: message?.content || message?.text || message?.message || "" };
  }

  function mergeChatMessages(incoming) {
    const merged = incoming.map(normalizeMessage).filter((message) => message.content);
    for (const local of chatMessages.filter((message) => message._local)) {
      const same = merged.findIndex((message) => message.role === local.role && message.content === local.content
        && (message.message_kind || "request") === (local.message_kind || "request"));
      if (same < 0) merged.push(local);
      else if (local._chat_error) merged[same] = { ...merged[same], ...local };
    }
    chatMessages = merged.map((message, index) => ({ message, index, at: Date.parse(message.at || message.ts || "") }))
      .sort((left, right) => Number.isFinite(left.at) && Number.isFinite(right.at) ? left.at - right.at || left.index - right.index : left.index - right.index)
      .map((item) => item.message).slice(-100);
    renderChat();
  }

  function chatMessageKind() {
    return $("chatMessageKind").value === "directive" ? "directive" : "request";
  }

  function renderChatKind() {
    const directive = chatMessageKind() === "directive";
    $("chatMessageKind").disabled = chatPending;
    $("chatKindHint").textContent = directive ? "Сохраняется до явного снятия и учитывается в будущих решениях. Команды сейчас не выполняются." : "Вопрос или команда выполняется один раз и не становится указанием на будущее.";
    $("chatInput").placeholder = directive ? "Задайте правило для будущих решений агента…" : "Спросите о системе или задайте команду…";
  }

  function setChatPending(pending) {
    chatPending = pending;
    $("chatBusy").hidden = !pending;
    $("chatSendButton").disabled = pending || !token;
    $("chatMessages").querySelectorAll("[data-chat-retry]").forEach((button) => { button.disabled = pending || !token; });
    renderChatKind();
    renderDirectControls();
    renderAutonomySetup();
  }

  function chatFailure(error, originalMessage, messageKind = "request") {
    const raw = typeof error?.message === "string" ? error.message : "";
    let message, retryMode = "draft";
    if (/json_syntax|schema_fields|structured.output|truncated_response/i.test(raw)) {
      message = "DeepSeek вернул ответ в неподходящем формате. Не удалось обработать ответ; команды из него не отправлены. Можно повторить запрос.";
      retryMode = "send";
    } else if (/429|rate.limit|лимит/i.test(raw)) {
      message = "DeepSeek временно ограничил запросы. Повторите запрос немного позже.";
      retryMode = "send";
    } else if (/таймаут|timed.out|timeout|не ответил/i.test(raw)) {
      message = "Ответ не получен за отведённое время. Если запрос содержал команду оборудованию, перед повтором сверьте её результат по телеметрии и журналу.";
    } else if (/401|403|DEEPSEEK_API_KEY|ключ.*DeepSeek|DeepSeek.*ключ/i.test(raw)) {
      message = "Не удалось обратиться к DeepSeek: проверьте ключ доступа и настройки API.";
    } else if (/модел|model|policy/i.test(raw)) {
      message = "Настроенная модель DeepSeek недоступна или не соответствует выбранному профилю. Проверьте настройки модели.";
    } else if (/нет связи|network|fetch|сервер интерфейса/i.test(raw)) {
      message = "Связь с сервером прервалась. Ответ не получен; перед повтором команды оборудованию сверьте состояние и журнал.";
    } else {
      const safe = raw.replace(/\bsk-[A-Za-z0-9_-]{8,}\b/g, "[СКРЫТО]");
      message = safe && !/[{}\[\]]|authorization|bearer/i.test(safe) ? safe.slice(0, 550) : "Запрос не завершён. Проверьте соединение, права и журнал агента, затем повторите.";
    }
    if (messageKind === "directive" && retryMode === "send") {
      retryMode = "draft";
      message += " Перед повтором проверьте список сохранённых указаний.";
    }
    const existing = chatMessages.find((item) => item._chat_error && item._retry_message === originalMessage
      && item._retry_message_kind === messageKind && item.content === message);
    if (existing) return;
    appendChat({ id: `local-chat-error-${++chatErrorSequence}`, role: "system", content: message,
                 at: new Date().toISOString(), _local: true, _chat_error: true,
                 _retry_message: originalMessage, _retry_message_kind: messageKind, _retry_mode: retryMode });
  }

  function appendChat(message) {
    const normalized = normalizeMessage(message);
    if (!normalized.content) return;
    const identity = normalized.id || normalized.message_id;
    if (identity) {
      const existing = chatMessages.findIndex((item) => item.id === identity || item.message_id === identity);
      if (existing >= 0) { chatMessages[existing] = { ...chatMessages[existing], ...normalized }; renderChat(); return; }
    }
    const last = chatMessages.at(-1);
    if (last && last.role === normalized.role && last.content === normalized.content
        && (last.message_kind || "request") === (normalized.message_kind || "request")) { chatMessages[chatMessages.length - 1] = { ...last, ...normalized }; renderChat(); return; }
    chatMessages.push(normalized);
    chatMessages = chatMessages.slice(-100);
    renderChat();
  }

  function renderChat() {
    if (!chatMessages.length) return;
    const signature = JSON.stringify(chatMessages.map((message) => [message.role, message.message_kind, message.content, message.commands, message.sources, message.rag_refs, message.retrieved_sources, message._chat_error, message._retry_mode, message._retry_message_kind]));
    if (signature === renderedChatSignature) return;
    renderedChatSignature = signature;
    const target = $("chatMessages");
    const previousScrollTop = target.scrollTop;
    const keepBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 75;
    target.replaceChildren();
    chatMessages.forEach((message) => {
      const role = message.role === "user" ? "user" : message.role === "system" ? "system" : "assistant";
      const wrapper = el("article", `chat-message ${role}`);
      wrapper.append(el("div", "chat-message-label", role === "user" ? message.message_kind === "directive" ? "ВЫ · УКАЗАНИЕ НА БУДУЩЕЕ" : "ВЫ" : role === "system" ? "СИСТЕМА" : "DEEPSEEK"), el("div", "chat-bubble", plain(message.content, 24000)));
      if (message._chat_error && message._retry_message) {
        wrapper.classList.add("chat-error");
        const retry = el("button", "chat-retry-button", message._retry_mode === "send" ? "Повторить запрос" : "Вернуть запрос в поле");
        retry.type = "button";
        retry.dataset.chatRetry = "true";
        retry.disabled = chatPending || !token;
        retry.addEventListener("click", () => {
          if (chatPending) return;
          $("chatMessageKind").value = message._retry_message_kind === "directive" ? "directive" : "request";
          renderChatKind();
          $("chatInput").value = message._retry_message;
          $("chatInput").focus();
          if (message._retry_mode === "send") {
            chatMessages = chatMessages.filter((item) => item.id !== message.id);
            renderChat();
            $("chatForm").requestSubmit();
          }
        });
        wrapper.append(retry);
      }
      const commands = commandResults(message.commands);
      if (commands) wrapper.append(commands);
      const usedSources = Array.isArray(message.sources) ? message.sources : [];
      const sources = sourceGroup(usedSources.length ? usedSources : message.rag_refs, usedSources.length ? "Источники ответа" : "Найденные источники");
      if (sources) wrapper.append(sources);
      if (!sources && Array.isArray(message.retrieved_sources)) {
        const found = sourceGroup(message.retrieved_sources, "Найденные источники");
        if (found) wrapper.append(found);
      }
      target.append(wrapper);
    });
    if (keepBottom) target.scrollTop = target.scrollHeight;
    else target.scrollTop = Math.min(previousScrollTop, Math.max(0, target.scrollHeight - target.clientHeight));
  }

  function renderUploadAvailability() {
    const selected = $("fileDevice").value;
    const controls = catalogueNode(selected).controls;
    const supportsUpload = Array.isArray(controls) && controls.includes("upload_file");
    const activeTeam = isConnected() && snapshot.connection?.role === "team";
    $("uploadButton").disabled = uploadPending || !token || !activeTeam || !supportsUpload || !$("organizerPrepared").checked;
    $("machineFile").disabled = uploadPending || !activeTeam || !supportsUpload;
    $("organizerPrepared").disabled = uploadPending || !activeTeam || !supportsUpload;
    $("refreshFilesButton").disabled = !token || !activeTeam;
    $("startJobButton").disabled = jobStartPending || !token || !activeTeam || !selectedRemoteFile || !Array.isArray(controls) || !controls.includes("start_job");
    $("remoteDeviceLabel").textContent = selected === "printer_3d_01" ? "3D-принтер" : "Фрезерный станок";
    if (!uploadPending && !$("machineFile").files.length) {
      $("selectedFileLabel").textContent = !activeTeam ? "Подключитесь с правами команды" : supportsUpload ? "Выбрать файл" : "На устройстве используются серверные файлы";
      $("selectedFileDetails").textContent = activeTeam && !supportsUpload ? "upload_file отсутствует в текущем каталоге API" : "G-code / NC · текст UTF-8 · до 16 МБ";
    }
    renderDirectControls();
    renderAutonomySetup();
  }

  function renderFileChoice() {
    $("organizerPrepared").checked = false;
    const file = $("machineFile").files[0];
    if (!file) { renderUploadAvailability(); return; }
    $("selectedFileLabel").textContent = file.name;
    $("selectedFileDetails").textContent = `${formatNumber(file.size / 1024, 1)} КБ · файл будет передан без запуска станка`;
    $("uploadStatus").textContent = "";
    $("uploadStatus").className = "form-status";
  }

  async function refreshFiles() {
    const device = $("fileDevice").value;
    const button = $("refreshFilesButton");
    button.disabled = true;
    empty($("remoteFiles"), "Запрашиваем список файлов", "Ответ поступает через API Купола.", "↻");
    try {
      const response = await api(`/api/files?node_id=${encodeURIComponent(device)}`);
      if (device !== $("fileDevice").value) return;
      const files = Array.isArray(response) ? response : response.files || response.result || [];
      renderFiles(files);
      remoteFilesDevice = device;
    } catch (error) {
      empty($("remoteFiles"), "Список файлов не получен", error.message, "!");
    } finally { renderUploadAvailability(); }
  }

  function renderFiles(files) {
    if (!Array.isArray(files) || !files.length) { selectedRemoteFile = ""; $("selectedRemoteFile").textContent = "Выберите файл из каталога устройства"; empty($("remoteFiles"), "Файлов пока нет", "Каталог выбранного устройства пуст или не содержит доступных программ.", "▤"); renderUploadAvailability(); return; }
    if (!files.some((item) => (typeof item === "string" ? item : item.name || item.file_name || item.filename) === selectedRemoteFile)) selectedRemoteFile = "";
    const target = $("remoteFiles");
    target.replaceChildren();
    files.slice(0, 500).forEach((file) => {
      const record = typeof file === "string" ? { name: file } : file;
      const name = record.name || record.file_name || record.filename || "Без названия";
      const row = el("label", `remote-file selectable ${selectedRemoteFile === name ? "selected" : ""}`);
      row.append(el("div", "file-icon", plain(name).split(".").at(-1).toUpperCase().slice(0, 4)));
      const label = el("div", "remote-file-body");
      label.append(el("div", "remote-file-name", plain(name, 800)));
      const size = record.size ?? record.size_bytes;
      const meta = [];
      if (number(size)) meta.push(`${formatNumber(size / 1024, 1)} КБ`);
      if (record.valid !== undefined) meta.push(record.valid ? "Проверка API пройдена" : "API сообщает об ошибке программы");
      if (record.error) meta.push(plain(record.error, 500));
      if (meta.length) label.append(el("div", "remote-file-meta", meta.join(" · ")));
      row.append(label);
      const radio = el("input");
      radio.type = "radio";
      radio.name = "remote_file";
      radio.value = name;
      radio.checked = selectedRemoteFile === name;
      radio.setAttribute("aria-label", `Выбрать файл ${name}`);
      radio.addEventListener("change", () => {
        selectedRemoteFile = name;
        $("selectedRemoteFile").textContent = `Выбран файл: ${name}`;
        $("jobStartStatus").textContent = "";
        target.querySelectorAll(".remote-file").forEach((item) => item.classList.toggle("selected", item === row));
        renderUploadAvailability();
      });
      row.append(radio);
      target.append(row);
    });
    $("selectedRemoteFile").textContent = selectedRemoteFile ? `Выбран файл: ${selectedRemoteFile}` : "Выберите файл из каталога устройства";
    renderUploadAvailability();
  }

  function renderHistory() {
    const source = historyData || snapshot.history || {};
    const incidents = Array.isArray(source) ? source : source.incidents || source.cases || [];
    const target = $("historySummary");
    target.replaceChildren();
    const historyMetrics = [["Материалов в базе знаний", source.documents ?? source.document_count ?? source.chunks ?? "—", "Документация и история кризисов"], ["Сохранённых инцидентов", source.incident_count ?? (Array.isArray(incidents) ? incidents.length : "—"), "Память случаев и их происхождение"], ["Проверенных результатов", source.verified_count ?? source.verified_successes ?? "—", "Принятие команды не равно успеху"]];
    historyMetrics.forEach(([label, value, note]) => {
      const stat = el("article", "history-stat");
      stat.append(el("span", "", label), el("strong", "", number(value) ? formatNumber(value) : String(value)), el("small", "", note));
      target.append(stat);
    });
    const cases = $("caseHistory");
    if (!Array.isArray(incidents) || !incidents.length) { empty(cases, "Память готова к накоплению опыта", "Здесь появятся инциденты, выбранные сценарии и результаты проверки эффекта.", "◇"); return; }
    cases.replaceChildren();
    incidents.slice(0, 80).forEach((incident) => {
      const card = el("article", "case-item");
      const top = el("div", "case-top");
      const state = incident.outcome || incident.state || "OPEN";
      top.append(el("h4", "", plain(incident.title || incident.fault_code || incident.summary || incident.id, 600)), badge(labelState(state), /SUCCESS|RESOLVED|VERIFIED/.test(state) ? "good" : /FAILED|BLOCKED/.test(state) ? "warning" : "neutral"));
      card.append(top);
      const description = incident.description || incident.message || incident.environment;
      if (description) card.append(el("p", "", plain(description, 1500)));
      if (Array.isArray(incident.attempts) && incident.attempts.length) card.append(el("p", "", incident.attempts.slice(-3).map((attempt) => `${attempt.runbook_id || "Сценарий"}: ${labelState(attempt.state || attempt.outcome || "UNKNOWN")}`).join(" · ")));
      cases.append(card);
    });
  }

  async function refreshHistory() {
    $("refreshHistoryButton").disabled = true;
    try { historyData = await api("/api/history"); renderHistory(); }
    catch (error) { toast(error.message, "error"); }
    finally { $("refreshHistoryButton").disabled = false; }
  }

  function applyState(data) {
    if (!data || typeof data !== "object") return;
    snapshot = { ...snapshot, ...data, connection: { ...snapshot.connection, ...(data.connection || {}) } };
    const signature = `${telemetryStamp() ?? ""}:${snapshot.telemetry?.received_at ?? ""}`;
    if (signature !== lastTelemetrySignature && Object.keys(telemetryNodes()).length) { lastTelemetrySignature = signature; lastReceivedLocal = Date.now(); }
    lastStateAt = Date.now();
    if (Array.isArray(data.messages) && data.messages.length) {
      mergeChatMessages(data.messages);
    }
    renderConnection();
    renderMetrics();
    renderFreshness();
    renderDiagnostics();
    renderEvents();
    if (currentView === "telemetry") renderNodes();
    else $("navNodeCount").textContent = String(Object.keys(telemetryNodes()).length);
    renderHistory();
  }

  function setStreamStatus(live, text) {
    streamLive = live;
    $("streamStatus").className = `stream-status ${live ? "live" : ""}`;
    $("streamStatus").replaceChildren(el("span", "status-dot"), document.createTextNode(text));
  }

  function openStream() {
    if (stopped) return;
    const url = new URL("/api/stream", window.location.href);
    url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    try { socket = new WebSocket(url); }
    catch (_) { setStreamStatus(false, "Нет связи с интерфейсом"); scheduleStreamRetry(); return; }
    socket.addEventListener("open", () => { socketDelay = 1000; setStreamStatus(true, "Живое обновление включено"); });
    socket.addEventListener("message", (event) => {
      let message;
      try { message = JSON.parse(event.data); } catch (_) { return; }
      if (message.type === "state") applyState(message.data);
      else if (message.type === "chat") appendChat(message.data);
      else if (message.type === "event") {
        recentEvents.push(message.data);
        recentEvents = recentEvents.slice(-200);
        snapshot.events = recentEvents;
        renderEvents();
      }
    });
    socket.addEventListener("close", () => { setStreamStatus(false, "Восстанавливаем связь с интерфейсом"); scheduleStreamRetry(); });
    socket.addEventListener("error", () => setStreamStatus(false, "Поток интерфейса недоступен"));
  }

  function scheduleStreamRetry() {
    if (stopped || socketRetry) return;
    socketRetry = window.setTimeout(async () => {
      socketRetry = null;
      try { applyState(await api("/api/state")); } catch (_) { /* The stream status already explains the outage. */ }
      openStream();
    }, socketDelay);
    socketDelay = Math.min(15000, socketDelay * 1.7);
  }

  function setView() {
    const requested = window.location.hash.slice(1) || "overview";
    currentView = Object.hasOwn(titleByView, requested) ? requested : "overview";
    Object.keys(titleByView).forEach((view) => { $(`${view}View`).hidden = view !== currentView; });
    document.querySelectorAll("[data-view]").forEach((item) => {
      const active = item.dataset.view === currentView;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
    });
    $("pageTitle").textContent = titleByView[currentView];
    if (currentView === "telemetry") renderNodes();
    if (currentView === "files" && !remoteFilesDevice) empty($("remoteFiles"), "Список файлов ещё не запрошен", "Подключите Купол и нажмите «Обновить».", "▤");
    if (currentView === "history") renderHistory();
  }

  $("connectionForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const address = $("domeAddress").value.trim();
    if (!address || connectionPending) return;
    const request = { address, profile: $("profileSelect").value, autonomous: $("autonomyToggle").checked };
    if ($("domeKey").value.trim()) request.dome_key = $("domeKey").value.trim();
    if ($("deepseekKey").value.trim()) request.deepseek_key = $("deepseekKey").value.trim();
    connectionPending = true;
    localConnectionError = "";
    $("connectionError").hidden = true;
    renderConnection();
    try {
      const response = await api("/api/connect", { method: "POST", body: request });
      $("domeKey").value = "";
      $("deepseekKey").value = "";
      $("credentialsDetails").open = false;
      if (response.connection) applyState(response); else applyState(await api("/api/state"));
    } catch (error) {
      localConnectionError = error.message;
      $("connectionError").textContent = error.message;
      $("connectionError").hidden = false;
      toast(error.message, "error");
    } finally { connectionPending = false; renderConnection(); }
  });

  $("disconnectButton").addEventListener("click", async () => {
    if (connectionPending) return;
    connectionPending = true;
    renderConnection();
    try { const response = await api("/api/disconnect", { method: "POST", body: {} }); if (response.connection) applyState(response); else applyState(await api("/api/state")); }
    catch (error) { toast(error.message, "error"); }
    finally { connectionPending = false; renderConnection(); }
  });

  $("profileSelect").addEventListener("change", () => { $("profileNotice").hidden = $("profileSelect").value !== "competition"; });
  $("autonomyToggle").addEventListener("change", async () => {
    if (autonomyPending) return;
    const enabled = $("autonomyToggle").checked;
    autonomyPending = true;
    renderAutonomySetup();
    renderDirectControls();
    try { const response = await api("/api/autonomy", { method: "POST", body: { enabled } }); if (response.connection || response.autonomous !== undefined) applyState(response); else applyState(await api("/api/state")); }
    catch (error) { $("autonomyToggle").checked = Boolean(snapshot.autonomous); toast(error.message, "error"); }
    finally { autonomyPending = false; renderAutonomySetup(); renderDirectControls(); }
  });
  Object.values(autonomyFields).forEach((id) => $(id).addEventListener("input", () => {
    autonomyConfigDirty = true;
    $("autonomySafetyConfirmed").checked = false;
    renderAutonomySetup();
  }));
  $("autonomySafetyConfirmed").addEventListener("change", renderAutonomyConfiguration);
  $("autonomyConfigForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    renderAutonomyConfiguration();
    if (autonomyConfigPending || $("saveAutonomyConfig").disabled) return;
    const payload = { ...autonomyCriteria(), safety_confirmed: $("autonomySafetyConfirmed").checked };
    autonomyConfigPending = true;
    $("autonomyConfigStatus").textContent = "";
    renderAutonomySetup();
    try {
      const response = await api("/api/autonomy/config", { method: "POST", body: payload });
      autonomyConfigDirty = false;
      autonomyConfigSignature = "";
      if (response.state) applyState(response.state);
      else if (response.autonomy_settings || response.connection) applyState(response);
      else applyState(await api("/api/state"));
      $("autonomyConfigStatus").textContent = "Параметры сохранены и подтверждены для текущей сессии. Автоуправление остаётся выключенным; при необходимости включите его переключателем.";
      $("autonomyConfigStatus").className = "form-status success";
    } catch (error) {
      $("autonomyConfigStatus").textContent = `Настройки не подтверждены: ${error.message}`;
      $("autonomyConfigStatus").className = "form-status error";
    } finally {
      autonomyConfigPending = false;
      $("autonomySafetyConfirmed").checked = false;
      renderAutonomySetup(); renderDirectControls();
    }
  });

  $("chatForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = $("chatInput").value.trim();
    if (!message || chatPending) return;
    const messageKind = chatMessageKind();
    setChatPending(true);
    appendChat({ role: "user", content: message, message_kind: messageKind, at: new Date().toISOString(), _local: true });
    $("chatInput").value = "";
    $("chatInput").style.height = "";
    try {
      const response = await api("/api/chat", { method: "POST", body: { message, message_kind: messageKind }, timeout: 120000 });
      if (Array.isArray(response.messages)) {
        if (!response.messages.some((item) => item?.role === "assistant" && typeof normalizeMessage(item).content === "string" && normalizeMessage(item).content.trim())) throw new Error("DeepSeek вернул пустой ответ. Запрос не завершён; перед повтором проверьте журнал агента.");
        mergeChatMessages(response.messages);
      } else if (response.role || typeof response.content === "string") {
        const answer = normalizeMessage(response);
        if (typeof answer.content !== "string" || !answer.content.trim()) throw new Error("DeepSeek вернул пустой ответ. Запрос не завершён; перед повтором проверьте журнал агента.");
        appendChat(answer);
      }
      else {
        const reply = response.reply || response.answer || response.content || response.message;
        const answer = normalizeMessage(typeof reply === "object" ? { ...response, ...reply } : { ...response, role: "assistant", content: reply });
        if (typeof answer.content !== "string" || !answer.content.trim()) throw new Error("DeepSeek вернул пустой ответ. Запрос не завершён; перед повтором проверьте журнал агента.");
        appendChat(answer);
      }
      if (response.state) applyState(response.state);
    } catch (error) { chatFailure(error, message, messageKind); }
    finally { setChatPending(false); $("chatInput").focus(); }
  });

  $("chatInput").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("chatForm").requestSubmit(); } });
  $("chatMessageKind").addEventListener("change", renderChatKind);
  $("chatInput").addEventListener("input", () => { $("chatInput").style.height = "auto"; $("chatInput").style.height = `${Math.min(135, $("chatInput").scrollHeight)}px`; });
  document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => { $("chatMessageKind").value = "request"; renderChatKind(); $("chatInput").value = button.dataset.prompt; $("chatInput").focus(); }));
  $("directControlNode").addEventListener("change", () => { refreshDirectActions(); $("directControlValue").value = ""; renderDirectControls(); });
  $("directControlAction").addEventListener("change", () => { $("directControlValue").value = ""; renderDirectControls(); });
  $("directControlForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    renderDirectControls();
    if (directControlPending || $("directControlSend").disabled) return;
    const command = { type: "control", node_id: $("directControlNode").value, action: $("directControlAction").value };
    const resultBox = $("directControlResult");
    try {
      if (directValueActions.has(command.action)) command.value = parseDirectValue($("directControlValue").value);
    } catch (error) {
      resultBox.replaceChildren(el("p", "form-status error", error.message));
      return;
    }
    directControlPending = true;
    renderDirectControls();
    resultBox.replaceChildren(el("p", "form-status pending", "Запрос передаётся в API. Ожидается ответ и новая телеметрия…"));
    try {
      const response = await api("/api/control", { method: "POST", body: { request: command }, timeout: 45000 });
      const status = response.status;
      const texts = {
        STATE_CONFIRMED: "Запрошенное состояние подтверждено новой телеметрией. Физический результат требует отдельного подтверждения.",
        ACKNOWLEDGED_ONLY: "API принял команду, но новое состояние пока не подтверждено. Автоматического повтора нет.",
        UNKNOWN: "Результат передачи неизвестен. Перед повтором сверьте телеметрию и журнал.",
        REJECTED: "API отклонил команду.", BLOCKED: "Выполнение заблокировано проверками состояния или прав.",
        SUPERSEDED: "Более новая ручная операция заменила этот запрос.",
        SUPERSEDED_AFTER_SEND: "После отправки поступила новая ручная операция; результат требует сверки."
      };
      const kind = status === "STATE_CONFIRMED" ? "success" : ["REJECTED", "BLOCKED"].includes(status) ? "error" : "pending";
      resultBox.replaceChildren(el("p", `form-status ${kind}`, texts[status] || "Получен ответ API, но выполнение команды не подтверждено. Проверьте журнал."));
      const results = commandResults([{ ...response, request: response.request || command }]);
      if (results) resultBox.append(results);
      if (response.state) applyState(response.state);
    } catch (error) {
      resultBox.replaceChildren(el("p", "form-status error", `${error.message} Автоматического повтора нет. Перед новой отправкой сверьте телеметрию и журнал.`));
    } finally {
      directControlPending = false;
      renderDirectControls();
    }
  });
  $("nodeSearch").addEventListener("input", renderNodes);
  $("nodeFilter").addEventListener("change", renderNodes);
  $("expandNodesButton").addEventListener("click", () => { expandAll = !expandAll; if (!expandAll) openedNodes.clear(); $("expandNodesButton").textContent = expandAll ? "Свернуть параметры" : "Раскрыть параметры"; renderNodes(); });
  $("machineFile").addEventListener("change", renderFileChoice);
  $("organizerPrepared").addEventListener("change", renderUploadAvailability);
  $("fileDevice").addEventListener("change", () => { $("machineFile").value = ""; remoteFilesDevice = ""; selectedRemoteFile = ""; $("selectedRemoteFile").textContent = "Выберите файл из каталога устройства"; $("jobStartStatus").textContent = ""; renderUploadAvailability(); empty($("remoteFiles"), "Выбрано другое устройство", "Нажмите «Обновить», чтобы запросить его файлы.", "▤"); });
  $("refreshFilesButton").addEventListener("click", refreshFiles);
  $("startJobButton").addEventListener("click", async () => {
    if (!selectedRemoteFile || jobStartPending) return;
    jobStartPending = true;
    renderUploadAvailability();
    $("jobStartStatus").textContent = "DeepSeek и исполнитель проверяют запуск выбранного файла…";
    $("jobStartStatus").className = "form-status pending";
    try {
      const response = await api("/api/job/start", { method: "POST", body: { node_id: $("fileDevice").value, file_name: selectedRemoteFile }, timeout: 120000 });
      appendChat(response);
      const results = Array.isArray(response.commands) ? response.commands : [];
      const rejected = results.find((command) => command.status === "REJECTED" || command.status === "BLOCKED");
      const uncertain = results.find((command) => ["ACKNOWLEDGED_ONLY", "UNKNOWN", "SUPERSEDED_AFTER_SEND", "SUPERSEDED"].includes(command.status));
      $("jobStartStatus").textContent = rejected ? `Команда отклонена: ${plain(rejected.response?.error || rejected.error || rejected.message || rejected.status, 1000)}` : uncertain ? `${uncertain.status === "ACKNOWLEDGED_ONLY" ? "API принял команду, но начало выполнения не подтверждено." : "Запуск не подтверждён; результат требует сверки."} Подробности — в чате.` : results.some((command) => command.status === "STATE_CONFIRMED") ? "Начало выполнения подтверждено новой телеметрией. Это не подтверждение завершения задания." : response.message || response.summary || "Запрос обработан. Фактический запуск не подтверждён; подробности — в чате.";
      $("jobStartStatus").className = `form-status ${rejected ? "error" : uncertain || !results.some((command) => command.status === "STATE_CONFIRMED") ? "pending" : "success"}`;
      if (response.state) applyState(response.state);
    } catch (error) { $("jobStartStatus").textContent = error.message; $("jobStartStatus").className = "form-status error"; }
    finally { jobStartPending = false; renderUploadAvailability(); }
  });
  $("refreshHistoryButton").addEventListener("click", refreshHistory);
  $("requiredFumeContext").addEventListener("change", async () => {
    if (workContextPending || clearingDirectives) return;
    const requiredFume = $("requiredFumeContext").checked;
    workContextPending = true;
    workContextError = "";
    renderDirectives();
    try {
      const response = await api("/api/work-context", { method: "POST", body: { required_fume: requiredFume } });
      if (response.state) applyState(response.state);
      else if (response.work_context || response.connection) applyState(response);
      else applyState(await api("/api/state"));
    } catch (error) {
      workContextError = error.message;
      toast(error.message, "error");
    } finally { workContextPending = false; renderDirectives(); }
  });
  $("clearDirectivesButton").addEventListener("click", async () => {
    if (clearingDirectives || workContextPending) return;
    clearingDirectives = true;
    renderDirectives();
    try {
      const response = await api("/api/directives/clear", { method: "POST", body: {} });
      if (response.state) applyState(response.state);
      else if (response.directives) applyState(response);
      else applyState(await api("/api/state"));
      workContextError = "";
      toast("Указания на будущее сняты. Агент продолжает оценивать текущее состояние системы.", "success");
    } catch (error) { toast(error.message, "error"); }
    finally { clearingDirectives = false; renderDirectives(); }
  });
  $("uploadForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = $("machineFile").files[0];
    if (!file || uploadPending) return;
    if (!$("organizerPrepared").checked) { $("uploadStatus").textContent = "Отметьте, что выбранный G-code предоставлен организаторами."; $("uploadStatus").className = "form-status error"; return; }
    if (file.size > 16 * 1024 * 1024) { $("uploadStatus").textContent = "Размер файла превышает 16 МБ."; $("uploadStatus").className = "form-status error"; return; }
    if (!/\.(gcode|nc|tap|ngc|gco)$/i.test(file.name)) { $("uploadStatus").textContent = "Выберите текстовый файл G-code или NC."; $("uploadStatus").className = "form-status error"; return; }
    uploadPending = true;
    renderUploadAvailability();
    $("uploadStatus").textContent = "Передаём файл через API Купола…";
    $("uploadStatus").className = "form-status pending";
    const body = new FormData();
    body.append("node_id", $("fileDevice").value);
    body.append("file", file, file.name);
    if ($("organizerPrepared").checked) body.append("organizer_prepared", "true");
    try {
      const result = await api("/api/upload", { method: "POST", body, timeout: 120000 });
      const response = result.response && typeof result.response === "object" ? result.response : {};
      const acknowledged = response.success === true;
      const rejected = response.success === false || result.status === "REJECTED" || result.status === "BLOCKED";
      const uncertain = ["UNKNOWN", "SUPERSEDED", "SUPERSEDED_AFTER_SEND"].includes(result.status);
      const error = response.error || result.error || result.message;
      if (rejected) {
        $("uploadStatus").textContent = `API отклонил передачу файла${error ? `: ${plain(error, 1200)}` : "."}`;
        $("uploadStatus").className = "form-status error";
        toast("Передача файла отклонена API", "error");
      } else if (acknowledged && result.listed === true && !result.list_error && !uncertain) {
        $("uploadStatus").textContent = `API принял команду, файл ${file.name} обнаружен в списке устройства. Запуск задания не выполнялся.`;
        $("uploadStatus").className = "form-status success";
        toast("Наличие файла подтверждено списком устройства", "success");
        await refreshFiles();
      } else {
        const reason = result.list_error || error;
        $("uploadStatus").textContent = `${acknowledged ? "API принял команду, но наличие файла на устройстве не подтверждено." : "Результат передачи файла неизвестен."}${reason ? ` ${plain(reason, 1200)}` : ""} Автоматического повтора нет. Перед ручной повторной передачей обновите список файлов и сверьте состояние.`;
        $("uploadStatus").className = "form-status pending";
        toast("Передача файла требует проверки; не повторяйте её вслепую");
      }
    } catch (error) { $("uploadStatus").textContent = error.message; $("uploadStatus").className = "form-status error"; }
    finally { uploadPending = false; renderUploadAvailability(); }
  });

  for (const name of ["dragenter", "dragover"]) $("fileDrop").addEventListener(name, () => $("fileDrop").classList.add("dragover"));
  for (const name of ["dragleave", "drop"]) $("fileDrop").addEventListener(name, () => $("fileDrop").classList.remove("dragover"));
  window.addEventListener("hashchange", setView);
  window.addEventListener("beforeunload", () => { stopped = true; if (socketRetry) window.clearTimeout(socketRetry); if (socket) socket.close(); });

  function clockTick() {
    $("localClock").textContent = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date());
    renderFreshness();
  }

  async function start() {
    setView();
    renderConnection(); renderMetrics(); renderDiagnostics(); renderEvents(); renderHistory(); clockTick();
    window.setInterval(clockTick, 1000);
    try {
      const session = await api("/api/session");
      token = session.token || "";
      if (!token) throw new Error("Сервер не выдал сессию веб-интерфейса.");
      applyState(await api("/api/state"));
      openStream();
    } catch (error) {
      setStreamStatus(false, "Локальный сервер недоступен");
      localConnectionError = `${error.message} Запустите локальный веб-сервер проекта.`;
      $("connectionError").textContent = localConnectionError;
      $("connectionError").hidden = false;
      renderConnection();
      $("connectionError").hidden = false;
    }
  }

  start();
})();
