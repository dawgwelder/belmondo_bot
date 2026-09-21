"use strict";

// Death Mission HTML5 controller.
//
// The server sends structured state (resources, actions with both previewed
// branches, finale odds, events). This file keeps one DOM skeleton per screen
// and updates values in place, so polling and requests never wipe what the
// player is reading. Confirmations are in-page; there is no window.confirm.
window.startDeathMission = (initial, api) => {
  const root = document.getElementById("death-mission");
  const TERMINAL = new Set(["won", "lost", "extracted", "timed_out", "cancelled_refunded", "expired", "lost_race"]);
  const ERRORS = {
    STALE_STAKE: "Состав изменился. Проверьте ставку и подтвердите заново.",
    STALE_REVISION: "Другой экран изменил состояние. Данные обновлены.",
    RUN_IN_PROGRESS: "Вы уже на операции в другом чате.",
    INSUFFICIENT_AGENTS: "Нет доступных агентов для ставки.",
    CONFIRMATION_EXPIRED: "Подтверждение истекло. Выберите режим заново.",
    INVALID_ACTION: "Действие недоступно.",
    EXTRACTION_LOCKED: "Эвакуация пока не открыта.",
    ALREADY_FINISHED: "Операция завершена.",
  };
  const OUTCOMES = {
    won: ["✓", "Операция выполнена", "Группа прошла финальный объект. Сеть возвращается удвоенной."],
    lost: ["×", "Связь потеряна", "Группа уничтожена. Ставка потеряна."],
    extracted: ["◆", "Аварийная эвакуация", "Часть сети выведена по аварийному каналу."],
    timed_out: ["⌛", "Время операции истекло", "Миссия закрыта по сроку."],
    cancelled_refunded: ["◆", "Операция отменена", "Ставка возвращена целиком."],
    expired: ["⌛", "Вход в операцию закрыт", "Событие завершилось до старта."],
    lost_race: ["◆", "Операцию занял другой агент", "Ваша сеть осталась дома."],
  };
  const BONUSES = [["tier3", "Два агента Tier 3", "Один случайный тип, ×2"], ["tier4", "Один агент Tier 4", "Самый редкий класс, ×1"]];
  const GLYPH = { hp: "❤️", intel: "🧠", alarm: "🚨" };
  const NAMES = { hp: "Состояние", intel: "Разведданные", alarm: "Тревога" };
  const HOLD_MS = 3000;

  const model = {
    state: initial,
    previous: null,
    pending: null,
    busy: false,
    holding: null,
    confirmation: null,
    tactic: "balanced",
    bonus: "tier3",
    notice: null,
    offline: false,
  };
  let screen = null;
  let ui = {};
  let panelKey = null;

  document.title = "Смертельная операция · Spy Clicker";
  document.getElementById("operation-title").textContent = "Смертельная операция";
  document.getElementById("eyebrow").textContent = "ЦЕНТР СПЕЦОПЕРАЦИЙ";

  // ---- tiny DOM helpers -------------------------------------------------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function button(label, className, handler) {
    const node = el("button", className, label);
    node.type = "button";
    node.addEventListener("click", handler);
    return node;
  }

  function signed(value) {
    return value > 0 ? `+${value}` : `−${-value}`;
  }

  function bundleText(bundle) {
    return (bundle || []).map((a) => `${a.emoji} ${a.name} ×${a.amount}`).join(", ") || "нет";
  }

  function bundleList(bundle) {
    const list = el("ul", "stake-list");
    (bundle || []).forEach((agent) => {
      const item = el("li", "stake-row");
      item.append(el("span", "stake-emoji", agent.emoji), el("span", "stake-name", agent.name), el("strong", "stake-amount", `×${agent.amount}`));
      list.append(item);
    });
    if (!(bundle || []).length) list.append(el("li", "stake-row muted", "нет"));
    return list;
  }

  function locked() {
    return model.busy || Boolean(model.pending);
  }

  // ---- transport -------------------------------------------------------

  async function send(action, choice = {}) {
    if (locked()) return;
    model.pending = { action, body: { revision: model.state.revision, operation_id: crypto.randomUUID(), choice } };
    model.notice = null;
    model.offline = false;
    await retry();
  }

  async function retry() {
    if (model.busy || !model.pending) return;
    model.busy = true;
    model.offline = false;
    render();
    try {
      const result = await api(`death/${model.pending.action}`, { method: "POST", body: JSON.stringify(model.pending.body) });
      model.pending = null;
      model.confirmation = null;
      model.previous = model.state;
      model.state = result;
      // A replay can return an older revision. Always recover the latest view.
      try {
        const latest = await api("state");
        if (latest.revision >= model.state.revision) model.state = latest;
      } catch (_) { /* Saved response remains usable. */ }
      model.notice = result.error ? (ERRORS[result.error] || "Состояние изменилось. Проверьте экран.") : null;
    } catch (_) {
      model.offline = true;
      model.notice = "Ответ не получен. Повторите тот же запрос, чтобы узнать результат.";
    }
    model.busy = false;
    render();
  }

  // ---- screens: skeleton builders ---------------------------------------

  function mount(kind) {
    screen = kind;
    panelKey = null;
    ui = { buttons: [] };
    root.replaceChildren();
    ({ preview: buildPreview, armed: buildArmed, in_run: buildRun }[kind])();
    ui.notice = el("div", "feedback-block");
    root.append(ui.notice);
  }

  function buildPreview() {
    ui.stake = el("section", "death-card");
    ui.stake.append(el("p", "eyebrow", "СТАВКА · ВСЯ ДОСТУПНАЯ СЕТЬ"));
    ui.stakeList = el("div");
    ui.stake.append(ui.stakeList);
    ui.extraction = el("p", "muted small");
    ui.stake.append(ui.extraction);

    ui.modes = el("section", "death-card");
    ui.modes.append(el("p", "eyebrow", "РЕЖИМ"));
    ui.modeCopy = el("div", "mode-grid");
    ui.modes.append(ui.modeCopy);

    ui.tactics = el("section", "death-card");
    ui.tactics.append(el("p", "eyebrow", "ТАКТИКА ЛИЧНОЙ МИССИИ"));
    ui.tacticRow = el("div", "segmented");
    ui.tacticHint = el("p", "muted small");
    ui.tactics.append(ui.tacticRow, ui.tacticHint);

    ui.bonus = el("section", "death-card");
    ui.bonus.append(el("p", "eyebrow", "БОНУС ЗА ПОЛНОЕ ПРОХОЖДЕНИЕ"));
    ui.bonusRow = el("div", "choice-grid");
    ui.bonus.append(ui.bonusRow);

    ui.actions = el("div", "death-actions");
    root.append(ui.stake, ui.modes, ui.tactics, ui.bonus, ui.actions);
  }

  function buildArmed() {
    ui.summary = el("section", "death-card");
    ui.summary.append(el("p", "eyebrow", "ПОДТВЕРЖДЕНИЕ"));
    ui.summaryBody = el("div");
    ui.summary.append(ui.summaryBody);
    ui.actions = el("div", "death-actions");
    root.append(ui.summary, ui.actions);
  }

  function buildRun() {
    ui.hud = el("section", "hud");
    ui.meters = {};
    ["hp", "intel", "alarm"].forEach((key) => {
      const row = el("div", `meter-row meter-${key}`);
      row.append(el("span", "meter-label", `${GLYPH[key]} ${NAMES[key]}`));
      const segments = el("div", "segments");
      row.append(segments);
      const value = el("strong", "meter-value");
      const delta = el("span", "meter-delta");
      row.append(value, delta);
      ui.meters[key] = { row, segments, value, delta, cells: [] };
      ui.hud.append(row);
    });
    ui.raidHint = el("p", "muted small");
    ui.hud.append(ui.raidHint);

    ui.route = el("section", "route");
    ui.routeCells = [];
    for (let index = 0; index < 6; index += 1) {
      const cell = el("div", "route-node");
      const mark = el("span", "route-mark");
      const label = el("span", "route-label");
      cell.append(mark, label);
      ui.route.append(cell);
      ui.routeCells.push({ cell, mark, label });
    }
    ui.modules = el("div", "module-chips");

    ui.odds = el("section", "odds");
    ui.oddsValue = el("strong", "odds-value");
    ui.oddsBar = el("div", "odds-bar");
    ui.oddsFill = el("div", "odds-fill");
    ui.oddsBar.append(ui.oddsFill);
    ui.oddsCaption = el("p", "muted small");
    const oddsHead = el("div", "odds-head");
    oddsHead.append(el("span", "eyebrow", "🎯 ШАНС ПРОЙТИ ФИНАЛ"), ui.oddsValue);
    ui.odds.append(oddsHead, ui.oddsBar, ui.oddsCaption);

    ui.title = el("h2", "room-title");
    ui.subtitle = el("p", "muted small");
    ui.panel = el("section", "panel");
    ui.footer = el("div", "death-actions");

    ui.logSection = el("section", "log");
    ui.logSection.append(el("p", "eyebrow", "ЖУРНАЛ"));
    ui.logList = el("ol", "log-list");
    ui.logSection.append(ui.logList);
    root.append(ui.hud, ui.route, ui.modules, ui.odds, ui.title, ui.subtitle, ui.panel, ui.footer, ui.logSection);
  }

  // ---- screens: updates --------------------------------------------------

  function render() {
    const state = model.state;
    if (TERMINAL.has(state.status)) {
      renderTerminal();
      return;
    }
    const kind = ["preview", "armed", "in_run"].includes(state.status) ? state.status : null;
    if (!kind) {
      root.replaceChildren(el("p", "death-copy", "Операция недоступна. Откройте её из Telegram."));
      screen = null;
      return;
    }
    if (screen !== kind) mount(kind);
    ({ preview: updatePreview, armed: updateArmed, in_run: updateRun }[kind])();
    updateNotice();
    ui.buttons.forEach((node) => { node.disabled = locked() || node.dataset.locked === "1"; });
  }

  function updateNotice() {
    ui.notice.replaceChildren();
    if (model.notice) ui.notice.append(el("p", "feedback", model.notice));
    if (model.offline && model.pending) {
      const retryButton = button("Повторить запрос", "action-button", retry);
      retryButton.disabled = model.busy;
      ui.notice.append(retryButton);
    }
  }

  function track(node, isLocked = false) {
    node.dataset.locked = isLocked ? "1" : "0";
    ui.buttons.push(node);
    return node;
  }

  function updatePreview() {
    const state = model.state;
    const rules = state.rules || {};
    const key = `${state.revision}:${model.tactic}:${model.bonus}`;
    if (panelKey === key) return;
    panelKey = key;
    ui.buttons = [];

    ui.stakeList.replaceChildren(bundleList(state.stake));
    ui.extraction.textContent = `🪂 Эвакуация после узла 3 вернёт: ${bundleText(state.extraction)}`;

    ui.modeCopy.replaceChildren();
    const allIn = el("div", "mode-card");
    allIn.append(el("strong", null, "🎲 All-in"), el("span", null, `Мгновенный исход, успех ${rules.all_in_percent}%. Успех: сеть ×${rules.multiplier} и Tier 3 ×1.`));
    const mission = el("div", "mode-card");
    mission.append(el("strong", null, "🕵️ Личная миссия"), el("span", null, `5 узлов и финальный объект. Победа: сеть ×${rules.multiplier} и выбранный бонус. Гибель — ставка потеряна.`));
    ui.modeCopy.append(allIn, mission);

    ui.tacticRow.replaceChildren();
    const tactics = state.tactics || [];
    if (!tactics.some((t) => t.id === model.tactic && !t.locked)) model.tactic = (tactics.find((t) => !t.locked) || { id: "balanced" }).id;
    tactics.forEach((tactic) => {
      const node = button(`${tactic.name}\n${GLYPH.hp}${tactic.hp} ${GLYPH.intel}${tactic.intel}`, "segment", () => { model.tactic = tactic.id; render(); });
      node.dataset.tactic = tactic.id;
      if (tactic.locked) node.classList.add("locked");
      if (tactic.id === model.tactic) node.classList.add("selected");
      ui.tacticRow.append(track(node, tactic.locked));
    });
    const selected = tactics.find((t) => t.id === model.tactic) || {};
    const notes = [];
    if (selected.risk_modifier) notes.push(`риск осложнений ${signed(selected.risk_modifier)} п.п.`);
    if (selected.shield) notes.push("первый полученный урон уменьшен на 1");
    const lockedHints = tactics.filter((t) => t.locked && t.unlock).map((t) => `🔒 ${t.name}: ${t.unlock}`);
    ui.tacticHint.textContent = [notes.length ? `${selected.name}: ${notes.join(", ")}.` : "", ...lockedHints].filter(Boolean).join("\n");

    ui.bonusRow.replaceChildren();
    BONUSES.forEach(([id, title, note]) => {
      const node = button("", "choice-card", () => { model.bonus = id; render(); });
      node.append(el("strong", null, title), el("span", "muted small", note));
      if (id === model.bonus) node.classList.add("selected");
      ui.bonusRow.append(track(node));
    });

    ui.actions.replaceChildren(
      track(button("🕵️ Пойти на миссию лично", "action-button", () => send("arm", { mode: "mission", tactic: model.tactic, bonus: model.bonus }))),
      track(button("🎲 All-in — без личного прохождения", "secondary-button", () => send("arm", { mode: "all_in", tactic: "balanced", bonus: "tier3" }))),
    );
  }

  function updateArmed() {
    const state = model.state;
    const key = `${state.revision}`;
    if (panelKey === key) return;
    panelKey = key;
    ui.buttons = [];
    ui.summaryBody.replaceChildren();
    const mode = state.mode === "all_in" ? "Мгновенный all-in" : "Личная миссия";
    ui.summaryBody.append(el("h2", "room-title", mode));
    if (state.mode === "mission") {
      const tactic = (state.tactics || []).find((t) => t.id === state.tactic);
      const bonus = BONUSES.find(([id]) => id === state.bonus);
      ui.summaryBody.append(el("p", null, `Тактика: ${tactic ? `${tactic.name} · ${GLYPH.hp}${tactic.hp} ${GLYPH.intel}${tactic.intel}` : state.tactic}`));
      ui.summaryBody.append(el("p", null, `Бонус финала: ${bonus ? bonus[1] : state.bonus}`));
      ui.summaryBody.append(el("p", "muted small", `Срок: ${Math.round((state.rules.seconds || 900) / 60)} мин. На таймауте — половина ставки, если эвакуация открыта; иначе 0.`));
    } else {
      ui.summaryBody.append(el("p", null, `Успех ${state.rules.all_in_percent}%: сеть ×${state.rules.multiplier} и один Tier 3. Провал: ставка потеряна.`));
    }
    ui.summaryBody.append(el("p", "eyebrow", "ОТПРАВЛЯЕТСЯ"), bundleList(state.stake));
    ui.summaryBody.append(el("p", "muted small", `🪂 Эвакуация вернёт: ${bundleText(state.extraction)}`));
    ui.summaryBody.append(el("p", "warning", "Подтверждение отправляет сеть на задание. Назад вернуть ставку нельзя."));
    ui.actions.replaceChildren(track(holdButton()), track(button("Назад к выбору", "secondary-button", () => send("back"))));
  }

  function holdButton() {
    const node = el("button", "action-button hold-button");
    node.type = "button";
    const label = el("span", "hold-label", "Удерживайте 3 секунды, чтобы поставить сеть");
    const ring = el("span", "hold-progress");
    node.append(ring, label);
    const cancel = () => {
      if (model.holding) window.clearTimeout(model.holding);
      model.holding = null;
      node.classList.remove("holding");
      node.style.setProperty("--progress", "0");
      label.textContent = "Удерживайте 3 секунды, чтобы поставить сеть";
    };
    const start = (event) => {
      if (model.holding || locked()) return;
      if (event && event.pointerId !== undefined && node.setPointerCapture) {
        try { node.setPointerCapture(event.pointerId); } catch (_) { /* Not a pointer. */ }
      }
      node.classList.add("holding");
      node.style.setProperty("--progress", "1");
      label.textContent = "Подтверждение ставки… удерживайте";
      model.holding = window.setTimeout(() => {
        model.holding = null;
        node.classList.remove("holding");
        send("commit");
      }, HOLD_MS);
    };
    node.addEventListener("pointerdown", start);
    ["pointerup", "pointercancel", "lostpointercapture", "blur"].forEach((name) => node.addEventListener(name, cancel));
    node.addEventListener("keydown", (event) => {
      if (event.key === " " || event.key === "Enter") { event.preventDefault(); start(); }
    });
    node.addEventListener("keyup", cancel);
    return node;
  }

  function updateRun() {
    const state = model.state;
    const mission = state.mission;
    const previous = model.previous && model.previous.mission;
    updateMeters(mission, previous);
    updateRoute(mission);
    updateOdds(mission);
    updateLog(mission);
    const key = `${state.revision}:${model.confirmation ? model.confirmation.kind + (model.confirmation.action || "") : ""}`;
    if (panelKey !== key) {
      panelKey = key;
      ui.buttons = [];
      updatePanel(state);
    }
  }

  function updateMeters(mission, previous) {
    ["hp", "intel", "alarm"].forEach((key) => {
      const meter = ui.meters[key];
      const max = key === "alarm" ? mission.raid_alarm : key === "hp" ? mission.max_hp : mission.max_intel;
      const value = mission[key];
      if (meter.cells.length !== max) {
        meter.segments.replaceChildren();
        meter.cells = [];
        for (let index = 0; index < max; index += 1) {
          const cell = el("span", "seg");
          meter.segments.append(cell);
          meter.cells.push(cell);
        }
      }
      meter.cells.forEach((cell, index) => {
        cell.classList.toggle("on", index < value);
        cell.classList.toggle("raid", key === "alarm" && index === max - 1);
        cell.classList.toggle("warn", key === "alarm" && index === max - 2);
      });
      meter.value.textContent = `${value}/${max}`;
      meter.row.classList.toggle("critical", key === "hp" ? value <= 2 : key === "alarm" && value >= max - 1);
      const before = previous ? previous[key] : value;
      if (previous && before !== value) {
        meter.delta.textContent = signed(value - before);
        meter.delta.classList.add("bump", value - before < 0 ? "down" : "up");
        window.setTimeout(() => { meter.delta.classList.remove("bump", "down", "up"); meter.delta.textContent = ""; }, 1800);
      }
    });
    model.previous = null; // Deltas are shown once per state change.
    ui.raidHint.textContent = mission.alarm >= mission.raid_alarm - 1
      ? `Ещё +1 тревоги — облава: урон 2, тревога ${mission.raid_alarm - 2}.`
      : `Облава при тревоге ${mission.raid_alarm}: урон 2, тревога ${mission.raid_alarm - 2}.`;
  }

  function updateRoute(mission) {
    const current = mission.phase === "boss" ? 5 : mission.node;
    ui.routeCells.forEach(({ cell, mark, label }, index) => {
      cell.className = "route-node";
      if (index < current) cell.classList.add("done");
      if (index === current) cell.classList.add("current");
      if (index === 5) {
        cell.classList.add("finale");
        mark.textContent = "☠";
        label.textContent = mission.phase === "boss" ? `${mission.boss} · ${mission.phase_names[Math.min(2, mission.boss_phase)]}` : mission.boss;
      } else {
        const marks = [];
        if ((mission.module_nodes || []).includes(index)) marks.push("◆");
        if (index === mission.checkpoint_node) marks.push("🪂");
        mark.textContent = marks.join("") || String(index + 1);
        label.textContent = index === current && mission.phase === "action" ? mission.title : `Узел ${index + 1}`;
      }
    });
    ui.modules.replaceChildren();
    const chips = mission.module_names.length ? mission.module_names : ["Модулей пока нет"];
    chips.forEach((name) => ui.modules.append(el("span", mission.module_names.length ? "chip module" : "chip muted", name)));
  }

  function updateOdds(mission) {
    const odds = mission.odds;
    ui.oddsValue.textContent = odds === null || odds === undefined ? "—" : `${odds}%`;
    ui.oddsFill.style.setProperty("width", `${odds || 0}%`);
    ui.odds.classList.toggle("low", (odds || 0) < 25);
    ui.odds.classList.toggle("high", (odds || 0) >= 60);
    ui.oddsCaption.textContent = mission.phase === "boss"
      ? "Точный шанс пережить оставшиеся фазы при лучших решениях."
      : "Если войти на финальный объект прямо сейчас с текущими ресурсами и модулями. Узлы впереди могут это изменить.";
  }

  function updateLog(mission) {
    const events = (mission.events || []).slice(-5).reverse();
    ui.logList.replaceChildren();
    if (!events.length) ui.logList.append(el("li", "log-item muted", "Ходов пока нет."));
    events.forEach((event, index) => {
      const item = el("li", `log-item${index === 0 ? " latest" : ""}`);
      if (event.kind !== "action") { item.textContent = event.label; ui.logList.append(item); return; }
      const head = el("div", "log-head");
      head.append(el("span", "log-title", `${event.title} · ${event.label}`));
      head.append(el("span", `chip ${event.failed ? "bad" : "ok"}`, event.failed ? `осложнение ${event.risk}%` : (event.risk ? `без осложнения (${event.risk}%)` : "выполнено")));
      item.append(head);
      const deltas = el("div", "log-deltas");
      ["hp", "intel", "alarm"].forEach((key) => {
        if (event[key]) deltas.append(el("span", `chip ${event[key] < 0 === (key !== "alarm") ? "bad" : "ok"}`, `${GLYPH[key]} ${signed(event[key])}`));
      });
      if (event.absorbed) deltas.append(el("span", "chip", `🛡 поглощено ${event.absorbed}`));
      if (event.medic) deltas.append(el("span", "chip ok", "медик +1"));
      if (event.passport) deltas.append(el("span", "chip ok", "пропуск отменил облаву"));
      if (event.raid) deltas.append(el("span", "chip bad", "облава"));
      if (!deltas.children || !deltas.children.length) deltas.append(el("span", "chip muted", "без изменений"));
      item.append(deltas);
      ui.logList.append(item);
    });
  }

  function branchLine(prefix, branch, cls) {
    const line = el("div", `branch ${cls}`);
    line.append(el("span", "branch-prefix", prefix));
    line.append(el("span", "branch-values", `${GLYPH.hp}${branch.hp} ${GLYPH.intel}${branch.intel} ${GLYPH.alarm}${branch.alarm}`));
    const notes = [];
    if (branch.dead) notes.push("☠️ гибель группы");
    if (branch.raid) notes.push("облава");
    if (branch.passport) notes.push("пропуск");
    if (branch.absorbed) notes.push(`защита ${branch.absorbed}`);
    if (branch.medic) notes.push("медик +1");
    if (notes.length) line.append(el("span", `branch-notes${branch.dead ? " danger" : ""}`, notes.join(", ")));
    return line;
  }

  function oddsChip(value, base) {
    if (value === null || value === undefined) return el("span", "chip muted", "—");
    const delta = base === null || base === undefined ? 0 : value - base;
    const chip = el("span", `chip odds-chip ${delta > 0 ? "ok" : delta < 0 ? "bad" : ""}`, `финал ${value}%${delta ? ` (${signed(delta)})` : ""}`);
    return chip;
  }

  function actionCard(action, mission) {
    const card = el("article", "action-card");
    if (!action.enabled) card.classList.add("disabled");
    if (action.certain_death) card.classList.add("lethal");
    else if (action.may_die) card.classList.add("risky");
    const head = el("div", "card-head");
    head.append(el("strong", "card-title", action.label), oddsChip(action.odds, mission.odds));
    card.append(head);
    const effects = el("div", "effects");
    if (action.cost) effects.append(el("span", "chip cost", `${GLYPH.intel} −${action.cost}`));
    ["hp", "intel", "alarm"].forEach((key) => {
      if (action[key]) effects.append(el("span", `chip ${action[key] < 0 === (key !== "alarm") ? "bad" : "ok"}`, `${GLYPH[key]} ${signed(action[key])}`));
    });
    if (action.risk) effects.append(el("span", "chip risk", `⚠️ ${action.risk}%: ${GLYPH.hp} −${action.damage}, ${GLYPH.alarm} +1`));
    if (!effects.children || !effects.children.length) effects.append(el("span", "chip muted", "без изменения ресурсов"));
    card.append(effects);
    if (action.preview) {
      card.append(branchLine(action.risk ? `без осложнения (${100 - action.risk}%)` : "итог", action.preview.success, "success"));
      if (action.preview.failure) card.append(branchLine(`осложнение (${action.risk}%)`, action.preview.failure, "failure"));
    }
    if (action.certain_death) card.append(el("p", "danger small", "☠️ Любой исход уничтожает группу."));
    else if (action.may_die) card.append(el("p", "danger small", "⚠️ Осложнение уничтожит группу."));
    if (!action.enabled) card.append(el("p", "muted small", `Нужно ${GLYPH.intel} ${action.cost}.`));
    const go = button(action.enabled ? "Выполнить" : "Недоступно", action.certain_death ? "action-button danger-button" : "action-button", () => {
      if (action.may_die) { model.confirmation = { kind: "action", action: action.id }; panelKey = null; render(); return; }
      send("action", { id: action.id });
    });
    go.dataset.action = action.id;
    card.append(track(go, !action.enabled));
    return card;
  }

  function updatePanel(state) {
    const mission = state.mission;
    ui.panel.replaceChildren();
    ui.footer.replaceChildren();
    const extraction = mission.checkpoint;
    if (model.confirmation) {
      renderConfirmation(state);
      return;
    }
    if (mission.phase === "room") {
      ui.title.textContent = `Узел ${mission.node + 1}: выберите комнату`;
      ui.subtitle.textContent = "Пройти можно только одну. Внутри будет одно действие.";
      mission.actions.forEach((room) => {
        const card = el("article", "action-card room-card");
        card.append(el("strong", "card-title", room.label));
        card.append(el("p", "muted small", (room.options || []).join(" · ")));
        const go = button("Войти", "action-button", () => send("action", { id: room.id }));
        go.dataset.action = room.id;
        card.append(track(go));
        ui.panel.append(card);
      });
    } else if (mission.phase === "module") {
      ui.title.textContent = "Выберите модуль";
      const nextSlot = (mission.module_nodes || []).find((node) => node > mission.node);
      ui.subtitle.textContent = nextSlot
        ? `Действует до конца забега. Следующий слот — перед узлом ${nextSlot + 1}.`
        : "Действует до конца забега. Это последний слот.";
      mission.actions.forEach((module) => {
        const card = el("article", "action-card module-card");
        const head = el("div", "card-head");
        head.append(el("strong", "card-title", module.label), oddsChip(module.odds, mission.odds));
        card.append(head, el("p", "muted small", module.description));
        const go = button("Взять", "action-button", () => send("action", { id: module.id }));
        go.dataset.action = module.id;
        card.append(track(go));
        ui.panel.append(card);
      });
    } else {
      ui.title.textContent = mission.title;
      ui.subtitle.textContent = mission.phase === "boss"
        ? `Фаза ${mission.boss_phase + 1} из 3. Каждая фаза — отдельное решение и отдельная проверка.`
        : "Одно действие, затем группа идёт дальше.";
      mission.actions.forEach((action) => ui.panel.append(actionCard(action, mission)));
    }
    const exit = button(extraction ? `🪂 Эвакуироваться — вернёт ${bundleText(state.extraction)}` : "Сдаться (ставка будет потеряна)", "secondary-button", () => {
      model.confirmation = { kind: extraction ? "extract" : "abandon" };
      panelKey = null;
      render();
    });
    exit.dataset.action = extraction ? "extract" : "abandon";
    ui.footer.append(track(exit));
    if (!extraction) ui.footer.append(el("p", "muted small", `Эвакуация с половиной ставки откроется после узла ${mission.checkpoint_node}.`));
    if ((mission.rules_summary || []).length) {
      const rules = el("details", "rules-summary");
      rules.append(el("summary", "muted small", "Правила этого забега"));
      const list = el("ul", "muted small");
      mission.rules_summary.forEach((line) => list.append(el("li", null, line)));
      rules.append(list);
      ui.footer.append(rules);
    }
  }

  function renderConfirmation(state) {
    const mission = state.mission;
    const { kind } = model.confirmation;
    const box = el("section", "confirm-box");
    if (kind === "extract") {
      ui.title.textContent = "Завершить миссию?";
      ui.subtitle.textContent = "";
      const compare = el("div", "compare");
      const now = el("div", "compare-col");
      now.append(el("p", "eyebrow", "ЭВАКУАЦИЯ СЕЙЧАС"), el("strong", null, "Гарантированно"), bundleList(state.extraction));
      const later = el("div", "compare-col");
      later.append(el("p", "eyebrow", "ПРОДОЛЖИТЬ"), el("strong", null, `~${mission.odds}% на ×${state.rules.multiplier} и бонус`), el("p", "muted small", `Иначе ставка потеряна. Таймаут вернёт половину, пока эвакуация открыта.`));
      compare.append(now, later);
      box.append(compare);
      box.append(track(button("Подтвердить эвакуацию", "action-button", () => send("extract"))));
    } else if (kind === "abandon") {
      ui.title.textContent = "Сдаться?";
      ui.subtitle.textContent = "";
      box.append(el("p", "warning", "Ставка будет потеряна целиком. Эвакуация ещё не открыта."));
      box.append(track(button("Подтвердить: сдаться", "action-button danger-button", () => send("abandon"))));
    } else {
      const action = mission.actions.find((a) => a.id === model.confirmation.action);
      ui.title.textContent = action ? action.label : "Опасное действие";
      ui.subtitle.textContent = "";
      if (action) {
        box.append(el("p", "warning", action.certain_death ? "☠️ Любой исход этого действия уничтожает группу." : "⚠️ При осложнении группа будет уничтожена."));
        if (action.preview) {
          box.append(branchLine(action.risk ? `без осложнения (${100 - action.risk}%)` : "итог", action.preview.success, "success"));
          if (action.preview.failure) box.append(branchLine(`осложнение (${action.risk}%)`, action.preview.failure, "failure"));
        }
        box.append(track(button("Всё равно выполнить", "action-button danger-button", () => send("action", { id: action.id }))));
      }
    }
    box.append(track(button("Продолжить миссию", "secondary-button", () => { model.confirmation = null; panelKey = null; render(); })));
    ui.panel.append(box);
  }

  function renderTerminal() {
    const state = model.state;
    const [icon, title, copy] = OUTCOMES[state.status] || ["◆", "Операция завершена", ""];
    const result = state.result || {};
    const resultIcon = document.getElementById("result-icon");
    resultIcon.textContent = icon;
    resultIcon.classList.toggle("danger", state.status === "lost" || state.status === "timed_out");
    document.getElementById("result-title").textContent = title;
    const lines = [copy];
    if (result.returned) lines.push(`Возвращено: ${bundleText(result.returned)}`);
    if (result.bonus && result.bonus.length) lines.push(`Бонус: ${bundleText(result.bonus)}`);
    const log = (state.mission && state.mission.log) || [];
    if (log.length) lines.push("Последние решения:\n" + log.slice(-3).map((line) => `• ${line}`).join("\n"));
    const unlocked = (state.tactics || []).filter((t) => !t.locked).map((t) => t.name);
    if (unlocked.length) lines.push(`Открытые тактики: ${unlocked.join(", ")}`);
    document.getElementById("result-copy").textContent = lines.filter(Boolean).join("\n\n");
    document.getElementById("result-score").textContent = "";
    document.getElementById("reward").textContent = "";
    const death = document.getElementById("death-mission");
    const resultScreen = document.getElementById("result");
    death.classList.add("hidden"); death.classList.remove("active");
    resultScreen.classList.remove("hidden"); resultScreen.classList.add("active");
    screen = "terminal";
  }

  // ---- boot ---------------------------------------------------------------

  render();
  const timer = window.setInterval(async () => {
    const state = model.state;
    const remaining = Math.max(0, Math.ceil((Date.parse(state.expires_at) - Date.now()) / 1000));
    const timerNode = document.getElementById("timer");
    timerNode.textContent = TERMINAL.has(state.status) ? "—" : `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
    if (timerNode.classList) timerNode.classList.toggle("urgent", !TERMINAL.has(state.status) && remaining <= 60);
    if (TERMINAL.has(state.status)) { window.clearInterval(timer); return; }
    if (locked() || model.holding) return;
    try {
      const latest = await api("state");
      // A slow poll must not overwrite a mutation that started meanwhile.
      if (locked() || model.holding) return;
      if (latest.revision >= state.revision) {
        const changed = latest.revision !== state.revision || latest.status !== state.status;
        if (changed) { model.previous = model.state; model.state = latest; render(); }
      }
    } catch (_) { /* Retry on the next tick; never settle in the browser. */ }
  }, 2000);
};
