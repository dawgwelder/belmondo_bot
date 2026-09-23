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
    DISABLED: "Игра отключена. Зарезервированный отряд возвращён.",
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
  const bonusChoices = (state) => state.bonuses || BONUSES.map(([id, name, description]) => ({ id, name, description, locked: false }));
  const bonusName = (state) => bonusChoices(state).find((b) => b.id === state.bonus)?.name || state.bonus;
  const victoryCopy = (state) => state.practice ? "завершение тренировки без наград" :
    `${state.victory ? bundleText(state.victory) : `сеть ×${state.rules.multiplier}`}${state.bonus === "none" ? "" : " и выбранный бонус"}`;
  const ROOM_ICONS = { patrol: "◉", archive: "▤", shelter: "⌂", cache: "▣", contact: "◇", ambush: "⚡" };
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
    reveal: null,
    revealedRevision: null,
  };
  let screen = null;
  let ui = {};
  let panelKey = null;
  let revealTimer = null;

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
    return model.busy || Boolean(model.pending) || Boolean(model.reveal);
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
      const request = model.pending;
      const before = model.state;
      const result = await api(`death/${model.pending.action}`, { method: "POST", body: JSON.stringify(model.pending.body) });
      model.pending = null;
      model.confirmation = null;
      model.previous = before;
      if (!Number.isInteger(result.revision) || result.revision >= model.state.revision) model.state = result;
      // A replay can return an older revision. Always recover the latest view.
      try {
        const latest = await api("state");
        if (latest.revision >= model.state.revision) model.state = latest;
      } catch (_) { /* Saved response remains usable. */ }
      model.notice = result.error ? (ERRORS[result.error] || "Состояние изменилось. Проверьте экран.") : null;
      // Reveal only this accepted move, never an obsolete replay or a poll.
      if (!result.error && result.revision > before.revision && model.state.revision === result.revision) {
        const check = checkForMove(before, result, request);
        if (check) startReveal(check, before);
      }
    } catch (_) {
      model.offline = true;
      model.notice = "Ответ не получен. Повторите тот же запрос, чтобы узнать результат.";
    }
    model.busy = false;
    render();
  }

  // ---- saved percentage checks ------------------------------------------

  function validCheck(check) {
    return check && Number.isInteger(check.roll) && check.roll >= 1 && check.roll <= 100
      && Number.isInteger(check.risk) && check.risk >= 0 && check.risk <= 100
      && check.failed === (check.roll <= check.risk);
  }

  function lastCheck(state) {
    if (validCheck(state.result?.check)) return state.result.check;
    return [...(state.mission?.events || [])].reverse().find(validCheck);
  }

  function checkForMove(before, after, request) {
    if (request.action === "commit" && before.mode === "all_in") {
      return validCheck(after.result?.check) ? after.result.check : null;
    }
    const oldEvents = before.mission?.events || [];
    const events = after.mission?.events || [];
    const check = events[events.length - 1];
    return request.action === "action" && events.length === oldEvents.length + 1
      && check?.action === request.body.choice.id && validCheck(check) ? check : null;
  }

  function checkOutcome(check) {
    if (check.kind === "all_in") return check.failed ? "Проигрыш" : "Выигрыш";
    return check.failed ? "Осложнение" : "Без осложнения";
  }

  function checkCard(check, rolling = false) {
    const card = el("section", `percent-check ${rolling ? "rolling" : check.failed ? "failed" : "passed"}`);
    card.ariaLabel = "Результат процентной проверки";
    card.append(el("p", "eyebrow", "ПРОЦЕНТНАЯ ПРОВЕРКА"), el("h2", "check-title", check.label));
    const outcome = el("p", "check-outcome", rolling ? "Раскрываем исход…" : checkOutcome(check));
    outcome.ariaLive = "polite";
    card.append(outcome, el("p", "check-number", rolling ? "… / 100" : `${check.roll} / 100`));
    const scale = el("div", "check-scale");
    scale.ariaHidden = "true";
    scale.style.setProperty("--risk", `${check.risk}%`);
    scale.style.setProperty("--roll-position", `${check.roll - 0.5}%`);
    scale.append(el("span", "check-marker"));
    const ticks = el("div", "check-ticks");
    ticks.ariaHidden = "true";
    ticks.append(el("span", null, "1"), el("span", null, "100"));
    card.append(scale, ticks);
    const bad = check.kind === "all_in" ? "Проигрыш" : "Осложнение";
    const good = check.kind === "all_in" ? "Выигрыш" : "Без осложнения";
    const legend = el("div", "check-legend");
    legend.append(el("span", "check-bad", `${bad}: ${check.risk}%`), el("span", "check-good", `${good}: ${100 - check.risk}%`));
    card.append(legend, el("p", "check-rule", check.risk === 0 ? "Осложнения нет при любом броске."
      : check.risk === 100 ? "Успешных значений нет."
      : `1–${check.risk} — ${bad.toLowerCase()}; ${check.risk + 1}–100 — ${good.toLowerCase()}.`));
    if (!rolling && check.kind !== "all_in") {
      const effects = el("div", "check-effects");
      ["hp", "intel", "alarm"].forEach((key) => {
        if (check[key]) effects.append(el("span", "chip", `${GLYPH[key]} ${signed(check[key])}`));
      });
      if (check.absorbed) effects.append(el("span", "chip", `🛡 Защита поглотила ${check.absorbed}`));
      if (check.medic) effects.append(el("span", "chip ok", "Медик +1"));
      if (check.passport) effects.append(el("span", "chip ok", "Пропуск отменил облаву"));
      if (check.raid) effects.append(el("span", "chip bad", "Облава"));
      if (!effects.children.length) effects.append(el("span", "chip", "Ресурсы не изменились"));
      card.append(effects);
    }
    return card;
  }

  function startReveal(check, before) {
    model.revealedRevision = model.state.revision;
    // Deterministic outcomes and reduced-motion users get the saved result directly.
    if (check.risk === 0 || check.risk === 100 || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
      || document.hidden) return;
    model.reveal = { check, before, phase: "rolling" };
    revealTimer = window.setTimeout(() => {
      if (!model.reveal) return;
      model.reveal.phase = "resolved";
      render();
      revealTimer = window.setTimeout(finishReveal, 1000);
    }, 850);
  }

  function finishReveal() {
    if (!model.reveal) return;
    window.clearTimeout(revealTimer);
    revealTimer = null;
    model.previous = model.reveal.before;
    model.reveal = null;
    render();
    const heading = TERMINAL.has(model.state.status) ? document.getElementById("result-title") : ui.title;
    if (heading) { heading.tabIndex = -1; heading.focus?.({ preventScroll: true }); }
  }

  function renderReveal() {
    const { check, before, phase } = model.reveal;
    if (screen === `reveal:${phase}`) return;
    screen = `reveal:${phase}`;
    const rolling = phase === "rolling";
    const card = checkCard(check, rolling);
    const skip = button(rolling ? "Показать результат сразу" : "Продолжить", "secondary-button", finishReveal);
    card.append(skip);
    const resources = before.mission;
    root.replaceChildren(card);
    if (resources && check.kind !== "all_in") {
      root.append(el("p", "muted small", `До хода: ❤️ ${resources.hp} · 🧠 ${resources.intel} · 🚨 ${resources.alarm}`));
    }
    root.append(el("p", "muted small", "Ход уже выполнен. Анимация показывает его результат."));
    window.scrollTo?.({ top: 0 });
    skip.focus?.({ preventScroll: true });
  }

  function updateLastCheck(state) {
    const check = lastCheck(state);
    const key = JSON.stringify(check || null);
    if (ui.lastCheck.dataset.key === key) return;
    ui.lastCheck.dataset.key = key;
    ui.lastCheck.replaceChildren();
    ui.lastCheck.classList.toggle("hidden", !check);
    if (!check) return;
    ui.lastCheck.open = state.revision === model.revealedRevision;
    ui.lastCheck.append(el("summary", "small", `Последний бросок: ${check.roll}/100 · ${checkOutcome(check).toLowerCase()}`), checkCard(check));
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
    ui.stakeHeading = el("p", "eyebrow");
    ui.stake.append(ui.stakeHeading);
    ui.stakeList = el("div");
    ui.stake.append(ui.stakeList);
    ui.extraction = el("p", "muted small");
    ui.stake.append(ui.extraction);
    ui.stakeDetails = el("div", "stake-details");
    ui.stake.append(ui.stakeDetails);

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
    ui.practice = el("p", "practice-banner");
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
    ui.lastCheck = el("details", "last-check");

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
    ui.support = el("div", "support-row");

    ui.odds = el("details", "odds");
    ui.oddsValue = el("strong", "odds-value");
    ui.oddsBar = el("div", "odds-bar");
    ui.oddsFill = el("div", "odds-fill");
    ui.oddsBar.append(ui.oddsFill);
    ui.oddsCaption = el("p", "muted small");
    const oddsHead = el("summary", "odds-head");
    oddsHead.append(el("span", "eyebrow", "🎯 ОЦЕНКА ФИНАЛА"), ui.oddsValue);
    ui.odds.append(oddsHead, ui.oddsBar, ui.oddsCaption);

    ui.title = el("h2", "room-title");
    ui.subtitle = el("p", "muted small");
    ui.panel = el("section", "panel");
    ui.scene = el("section", "mission-scene");
    ui.footer = el("div", "death-actions");

    ui.logSection = el("details", "log");
    ui.logSection.append(el("summary", "eyebrow", "ЖУРНАЛ РЕШЕНИЙ"));
    ui.logList = el("ol", "log-list");
    ui.logSection.append(ui.logList);
    root.append(ui.practice, ui.hud, ui.lastCheck, ui.route, ui.support, ui.modules, ui.title, ui.subtitle, ui.scene, ui.panel, ui.footer, ui.odds, ui.logSection);
  }

  // ---- screens: updates --------------------------------------------------

  function render() {
    const state = model.state;
    if (model.reveal) { renderReveal(); return; }
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
    const bonuses = bonusChoices(state);
    if (!bonuses.some((b) => b.id === model.bonus && !b.locked)) model.bonus = bonuses.find((b) => !b.locked)?.id;
    const key = `${state.revision}:${model.tactic}:${model.bonus}`;
    if (panelKey === key) return;
    panelKey = key;
    ui.buttons = [];

    ui.stakeList.replaceChildren(bundleList(state.stake));
    ui.stakeHeading.textContent = "СТАВКА · ВСЯ ДОСТУПНАЯ СЕТЬ";
    ui.stakeDetails.replaceChildren();
    (state.specialists || []).forEach((agent) => ui.stakeDetails.append(el("p", "support-note", `${agent.emoji} ${agent.name}: ${agent.ability}, −${agent.reduction} п.п. риска, 1 заряд.`)));
    if (state.victory) ui.stakeDetails.append(el("p", "small", `✓ Личная миссия вернёт при победе: ${bundleText(state.victory)}. Бонус — ниже.`));
    ui.extraction.textContent = `🪂 Эвакуация после узла 3 вернёт: ${bundleText(state.extraction)}`;

    ui.modeCopy.replaceChildren();
    const allIn = el("div", "mode-card");
    allIn.append(el("strong", null, "🎲 All-in"), el("span", null, `Мгновенный исход, успех ${rules.all_in_percent}%. Успех: сеть ×${rules.multiplier} и Tier 3 ×1.`));
    const mission = el("div", "mode-card");
    mission.append(el("strong", null, "🕵️ Личная миссия"), el("span", null, rules.version === "roguelite_v4"
      ? "Вся доступная сеть, карта района и три фазы финала. Победа возвращает сеть и по одному агенту за каждые пять одного типа. Гибель — потеря ставки."
      : `5 узлов и финальный объект. Победа: сеть ×${rules.multiplier}. Доступные бонусы указаны ниже. Гибель — ставка потеряна.`));
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
    bonuses.forEach(({ id, name: title, description: note, locked: bonusLocked }) => {
      const node = button("", "choice-card", () => { model.bonus = id; render(); });
      node.append(el("strong", null, title), el("span", "muted small", note));
      if (id === model.bonus) node.classList.add("selected");
      if (bonusLocked) node.classList.add("locked");
      ui.bonusRow.append(track(node, bonusLocked));
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
      ui.summaryBody.append(el("p", null, `Тактика: ${tactic ? `${tactic.name} · ${GLYPH.hp}${tactic.hp} ${GLYPH.intel}${tactic.intel}` : state.tactic}`));
      ui.summaryBody.append(el("p", null, `Бонус финала: ${bonusName(state)}`));
      if (state.victory) ui.summaryBody.append(el("p", null, `✓ При победе вернётся: ${bundleText(state.victory)}`));
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
    const holdCopy = "Удерживайте 3 секунды, чтобы поставить сеть";
    const label = el("span", "hold-label", holdCopy);
    const ring = el("span", "hold-progress");
    node.append(ring, label);
    const cancel = () => {
      if (model.holding) window.clearTimeout(model.holding);
      model.holding = null;
      node.classList.remove("holding");
      node.style.setProperty("--progress", "0");
      label.textContent = holdCopy;
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
    ui.practice.textContent = state.practice ? "ТРЕНИРОВКА · Виртуальный отряд. Без потерь, наград и достижений." : "";
    ui.practice.classList.toggle("hidden", !state.practice);
    updateMeters(mission, previous);
    updateRoute(mission);
    updateOdds(mission);
    updateLog(mission);
    updateLastCheck(state);
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
    ui.raidHint.textContent = mission.raid_hint || (mission.alarm >= mission.raid_alarm - 1
      ? `Ещё +1 тревоги — облава: урон 2, тревога ${mission.raid_alarm - 2}.`
      : `Облава при тревоге ${mission.raid_alarm}: урон 2, тревога ${mission.raid_alarm - 2}.`);
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
    if (mission.modules.includes("passport")) ui.modules.append(el("span", "chip", mission.passport_used ? "Пропуск: отмена потрачена · урон облав −1" : "Пропуск: отмена готова"));
    if (mission.modules.includes("armor")) ui.modules.append(el("span", "chip", mission.armor_used ? "Броня: сработала" : "Броня: готова"));
    if (mission.tactic === "assault") ui.modules.append(el("span", "chip", mission.assault_shield ? "Щит: готов" : "Щит: потрачен"));
    ui.support.replaceChildren();
    (mission.specialists || []).forEach((agent) => {
      const chip = el("div", `support-card${agent.ready ? "" : " spent"}`);
      chip.append(el("span", "support-portrait", agent.emoji), el("strong", null, agent.name), el("small", null, agent.ready ? `1 заряд · −${agent.reduction} п.п.` : "Заряд использован"));
      ui.support.append(chip);
    });
  }

  function updateOdds(mission) {
    if (ui.odds.dataset.phase !== mission.phase) ui.odds.open = mission.phase === "boss";
    ui.odds.dataset.phase = mission.phase;
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
      if (event.kind !== "action") { item.textContent = `${event.label}${event.intel ? ` · 🧠 ${signed(event.intel)}` : ""}`; ui.logList.append(item); return; }
      const head = el("div", "log-head");
      head.append(el("span", "log-title", `${event.title} · ${event.label}`));
      head.append(el("span", `chip ${event.failed ? "bad" : "ok"}`, event.failed ? `осложнение ${event.risk}%` : (event.risk ? `без осложнения (${event.risk}%)` : "выполнено")));
      item.append(head);
      const deltas = el("div", "log-deltas");
      if (validCheck(event)) deltas.append(el("span", "chip", `Бросок ${event.roll}/100`));
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
    if (action.risk) effects.append(el("span", "chip risk", `⚠️ Осложнение ${action.risk}% · базовый урон ${action.damage}${action.complication_alarm === 0 ? " · без тревоги" : " · 🚨 +1"}`));
    if (action.specialist) effects.append(el("span", "chip ok", `Риск ${action.base_risk}% → ${action.risk}% · 1 заряд`));
    if (!effects.children || !effects.children.length) effects.append(el("span", "chip muted", "без изменения ресурсов"));
    card.append(effects);
    if (action.preview) {
      const branches = el("details", "action-outcomes");
      branches.append(el("summary", "small", "Оба исхода и работа защиты"));
      branches.append(branchLine(action.risk ? `без осложнения (${100 - action.risk}%)` : "итог", action.preview.success, "success"));
      if (action.preview.failure) branches.append(branchLine(`осложнение (${action.risk}%)`, action.preview.failure, "failure"));
      card.append(branches);
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
    mission.actions.filter((variant) => variant.base_id === action.id).forEach((variant) => {
      const agent = mission.specialists.find((entry) => entry.id === variant.specialist);
      const support = button(`${agent.emoji} ${agent.ability} · риск ${variant.risk}%`, "secondary-button support-action", () => {
        if (variant.may_die) { model.confirmation = { kind: "action", action: variant.id }; panelKey = null; render(); return; }
        send("action", { id: variant.id });
      });
      support.dataset.action = variant.id;
      const details = el("details", "action-outcomes");
      details.append(el("summary", "small", `Со специалистом: ${variant.base_risk}% → ${variant.risk}% · 1 заряд`));
      if (variant.preview) {
        details.append(branchLine(`без осложнения (${100 - variant.risk}%)`, variant.preview.success, "success"));
        if (variant.preview.failure) details.append(branchLine(`осложнение (${variant.risk}%)`, variant.preview.failure, "failure"));
      } else {
        details.append(el("p", "muted small", `Нужно ${GLYPH.intel} ${variant.cost}.`));
      }
      if (variant.may_die) details.append(el("p", "danger small", "Осложнение всё ещё уничтожит группу."));
      card.append(track(support, !variant.enabled), details);
    });
    return card;
  }

  function renderScene(mission) {
    ui.scene.replaceChildren();
    const visible = ["action", "boss", "challenge"].includes(mission.phase);
    ui.scene.classList.toggle("hidden", !visible);
    if (!visible) return;
    const key = mission.phase === "boss" ? mission.boss_id || "hq" : mission.room_id || "patrol";
    // Authored SVG, no remote images or private map data. Controls remain real
    // accessible HTML buttons; the drawing sets the scene for their choices.
    const motifs = {
      patrol: '<path d="M80 128V50h85v78M185 128V25h110v103M320 128V65h75v63"/><path class="search-beam" d="M285 43l-130 95h230z"/><circle cx="282" cy="43" r="7"/><path d="M210 145h90m-65-8v-24m40 24v-24"/>',
      archive: '<path d="M90 143V35h300v108M110 54h80v65h-80zM290 54h80v65h-80z"/><path d="M120 72h60m-60 18h60m120-18h60m-60 18h60"/><rect x="209" y="65" width="60" height="50" rx="5"/><path class="scene-accent" d="M220 96l10-14 9 10 17-18M239 116v20m-20 0h40"/>',
      shelter: '<path d="M106 138V74l134-50 134 50v64M210 139V82h60v57"/><path d="M128 87h54v33h-54zM300 87h54v33h-54z"/><path class="scene-accent" d="M234 48h12v10h10v12h-10v10h-12V70h-10V58h10z"/>',
      cache: '<path d="M110 75l85-20 45 28v62l-85 15-45-30zM240 85l80-25 53 35v47l-77 24-56-21z"/><path d="M110 75l45 34 85-26m-85 26v51M240 85l56 38 77-28m-77 28v43"/><path class="scene-accent" d="M176 114h26m-13-13v26M315 129h23"/>',
      contact: '<path d="M85 144V30h65v114M330 144V30h65v114M150 42h180M150 67h180"/><circle cx="213" cy="88" r="13"/><path d="M191 135l8-31h28l8 31"/><circle cx="273" cy="88" r="13"/><path d="M253 135l8-31h25l8 31"/><path class="scene-accent" d="M228 115h29m-11-5 11 5-11 5"/>',
      ambush: '<path d="M85 120h310M105 90h270M119 70v65m65-65v65m65-65v65m65-65v65m45-65v65"/><path class="scene-danger" d="M200 55l40-45 40 45zM240 25v14m0 5v4"/><path d="M90 147l45-22m255 22-45-22"/>',
      hq: '<path d="M98 148V67h100V30h85v37h100v81M218 148V93h44v55"/><path d="M119 86h57m-57 19h57m-57 19h57m130-38h56m-56 19h56m-56 19h56"/><path class="scene-danger" d="M222 48h35m-17-9v26"/>',
      train: '<path d="M70 136h340M90 62h276l30 62H90zM110 80h44v24h-44zM176 80h44v24h-44zM242 80h44v24h-44zM308 80h51l12 24h-63z"/><circle cx="145" cy="128" r="13"/><circle cx="332" cy="128" r="13"/><path class="scene-accent" d="M50 82h25M40 101h35M36 120h39"/>',
    };
    const art = el("div", "scene-art");
    art.innerHTML = `<svg viewBox="0 0 480 180" aria-hidden="true"><path class="scene-ground" d="M0 157h480M25 177h430"/><g>${motifs[key] || motifs.patrol}</g><circle class="agent-dot" cx="52" cy="149" r="7"/></svg>`;
    ui.scene.append(art);
    if (mission.phase === "boss") {
      const phases = el("div", "finale-phases");
      mission.phase_names.forEach((name, index) => phases.append(el("span", `phase-pill ${index < mission.boss_phase ? "done" : index === mission.boss_phase ? "current" : ""}`, `${index < mission.boss_phase ? "✓" : index + 1} ${name}`)));
      ui.scene.append(phases);
    } else {
      const goals = {
        patrol: "Пройти мимо патруля: заплатить разведданными, рискнуть или пройти заграждения.",
        archive: "Добыть сведения для финального объекта.", shelter: "Восстановить группу или сбить преследование.",
        cache: "Выбрать припасы, которых не хватает отряду.", contact: "Решить, стоят ли сведения потери прикрытия.",
        ambush: "Выбраться из засады. Саботажник может подготовить прорыв.",
      };
      ui.scene.append(el("p", "scene-goal", goals[key] || "Выберите действие для отряда."));
    }
  }

  function renderMap(mission) {
    const map = el("div", "mission-map");
    map.ariaLabel = "Карта операции: пройденные узлы и доступные комнаты";
    mission.map.forEach((layer) => {
      const row = el("div", `map-layer ${layer.status}`);
      row.append(el("span", "map-index", layer.status === "visited" ? "✓" : layer.node + 1));
      const rooms = el("div", "map-rooms");
      if (layer.status === "current") {
        layer.rooms.forEach((room) => {
          const action = mission.actions.find((a) => a.id === room.id);
          const go = button("", "map-room", () => send("action", { id: room.id }));
          go.dataset.action = room.id;
          go.append(el("span", "map-icon", ROOM_ICONS[room.id] || "◇"), el("strong", null, room.label));
          go.append(el("small", "muted", (action?.options || []).join(" · ")));
          rooms.append(track(go));
        });
      } else if (layer.status === "visited") {
        rooms.append(el("div", "map-visited", layer.rooms[0]?.label || "Пройдено"));
      } else {
        rooms.append(el("div", "map-fog", "? Район не разведан"));
      }
      row.append(rooms);
      map.append(row);
    });
    const finale = el("div", "map-layer finale");
    finale.append(el("span", "map-index", "☠"), el("strong", "map-visited", `${mission.boss} · 3 фазы`));
    map.append(finale);
    ui.panel.append(map);
  }

  function renderChallenge(mission) {
    const challenge = mission.challenge;
    ui.title.textContent = "Восстановите цепь Архива";
    ui.subtitle.textContent = `Доведите сигнал ⚡ до выхода ◆. Нажимайте соседние подсвеченные клетки, обходите ×. Осталось ходов: ${challenge.moves_left}. Награда: 🧠 +1 (до ${mission.max_intel}).`;
    const board = el("div", "archive-board");
    board.ariaLabel = "Схема архива 4 на 4";
    for (let cell = 0; cell < challenge.size * challenge.size; cell += 1) {
      const id = `cell_${cell}`;
      const blocked = challenge.blocked.includes(cell);
      const current = challenge.path[challenge.path.length - 1] === cell;
      const visited = challenge.path.includes(cell);
      const allowed = mission.actions.some((a) => a.id === id);
      const label = current ? "⚡" : blocked ? "×" : cell === challenge.goal ? "◆" : visited ? "●" : "·";
      const node = button(label, `archive-cell${blocked ? " blocked" : ""}${current ? " current" : ""}${visited ? " visited" : ""}${allowed ? " reachable" : ""}`, () => send("action", { id }));
      node.dataset.cell = String(cell);
      node.ariaLabel = `Строка ${Math.floor(cell / challenge.size) + 1}, столбец ${cell % challenge.size + 1}: ${current ? "сигнал" : blocked ? "ловушка" : cell === challenge.goal ? "выход" : allowed ? "можно перейти" : "клетка"}`;
      board.append(track(node, !allowed));
    }
    ui.panel.append(board, track(button("Пропустить схему без штрафа", "secondary-button", () => send("action", { id: "skip_puzzle" }))));
  }

  function updatePanel(state) {
    const mission = state.mission;
    ui.panel.replaceChildren();
    ui.footer.replaceChildren();
    renderScene(mission);
    const extraction = mission.checkpoint;
    if (model.confirmation) {
      renderConfirmation(state);
      return;
    }
    if (mission.phase === "room") {
      ui.title.textContent = `Узел ${mission.node + 1}: выберите комнату`;
      ui.subtitle.textContent = "Пройти можно только одну. Внутри будет одно действие.";
      if (mission.map) renderMap(mission);
      else mission.actions.forEach((room) => {
        const card = el("article", "action-card room-card");
        card.append(el("strong", "card-title", room.label));
        card.append(el("p", "muted small", (room.options || []).join(" · ")));
        const go = button("Войти", "action-button", () => send("action", { id: room.id }));
        go.dataset.action = room.id;
        card.append(track(go));
        ui.panel.append(card);
      });
    } else if (mission.phase === "challenge") {
      renderChallenge(mission);
    } else if (mission.phase === "module") {
      ui.title.textContent = "Выберите модуль";
      const nextSlot = (mission.module_nodes || []).find((node) => node > mission.node);
      ui.subtitle.textContent = nextSlot
        ? `Действует до конца забега. Следующий слот — перед узлом ${nextSlot + 1}.`
        : "Действует до конца забега. Это последний слот.";
      ui.subtitle.textContent += " Проценты оценивают только финал с текущими ресурсами. Польза на оставшихся узлах и ранняя эвакуация в них не учтены.";
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
      mission.actions.filter((action) => !action.specialist).forEach((action) => ui.panel.append(actionCard(action, mission)));
    }
    const exit = button(state.practice ? "Завершить тренировку" : extraction ? `🪂 Эвакуироваться — вернёт ${bundleText(state.extraction)}` : "Сдаться (ставка будет потеряна)", "secondary-button", () => {
      model.confirmation = { kind: extraction ? "extract" : "abandon" };
      panelKey = null;
      render();
    });
    exit.dataset.action = extraction ? "extract" : "abandon";
    ui.footer.append(track(exit));
    if (!extraction && !state.practice) ui.footer.append(el("p", "muted small", `Эвакуация с половиной ставки откроется после узла ${mission.checkpoint_node}.`));
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
    if (state.practice && (kind === "extract" || kind === "abandon")) {
      ui.title.textContent = "Закончить тренировку?";
      ui.subtitle.textContent = "Ваша сеть не изменится. Новую попытку можно начать в Центре.";
      box.append(track(button("Закончить", "action-button", () => send(kind))));
    } else if (kind === "extract") {
      ui.title.textContent = "Завершить миссию?";
      ui.subtitle.textContent = "";
      const compare = el("div", "compare");
      const now = el("div", "compare-col");
      now.append(el("p", "eyebrow", "ЭВАКУАЦИЯ СЕЙЧАС"), el("strong", null, "Гарантированно"), bundleList(state.extraction));
      const later = el("div", "compare-col");
      later.append(el("p", "eyebrow", "ПРОДОЛЖИТЬ"), el("strong", null, `Победа: ${victoryCopy(state)}`));
      later.append(el("p", "muted small", mission.phase === "boss"
        ? `Шанс пройти оставшиеся фазы финала при лучших решениях: ${mission.odds}%.`
        : `Шанс всего оставшегося маршрута не рассчитан. Оценка ${mission.odds}% относится только к финалу, если войти в него сейчас; комнаты впереди изменят ресурсы.`));
      later.append(el("p", "muted small", "При гибели ставка потеряна. Таймаут вернёт половину, пока эвакуация открыта."));
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
    if (state.practice) {
      lines[0] = "Тренировка завершена. Настоящие агенты не участвовали; награды и достижения не выдаются.";
    } else if (state.status === "won") {
      lines[0] = state.mode === "all_in" ? "All-in выигран. Выплата указана ниже." : "Все три фазы пройдены. Отряд возвращается с наградой.";
    }
    if (result.returned) lines.push(`Возвращено: ${bundleText(result.returned)}`);
    if (result.bonus && result.bonus.length) lines.push(`Бонус: ${bundleText(result.bonus)}`);
    const log = (state.mission && state.mission.log) || [];
    if (log.length) lines.push("Последние решения:\n" + log.slice(-3).map((line) => `• ${line}`).join("\n"));
    const unlocked = (state.tactics || []).filter((t) => !t.locked).map((t) => t.name);
    if (unlocked.length) lines.push(`Открытые тактики: ${unlocked.join(", ")}`);
    document.getElementById("result-copy").textContent = lines.filter(Boolean).join("\n\n");
    const check = lastCheck(state);
    document.getElementById("death-result-check").replaceChildren(...(check ? [checkCard(check)] : []));
    document.getElementById("result-score").textContent = "";
    document.getElementById("reward").textContent = "";
    const back = el("a", "secondary-button return-to-center", "← В Центр · новая тренировка");
    back.href = "../";
    document.getElementById("reward").append(back);
    const death = document.getElementById("death-mission");
    const resultScreen = document.getElementById("result");
    if (model.holding) window.clearTimeout(model.holding);
    model.holding = null;
    model.confirmation = null;
    death.replaceChildren();
    death.classList.add("hidden"); death.classList.remove("active");
    resultScreen.classList.remove("hidden"); resultScreen.classList.add("active");
    if (screen !== "terminal") window.scrollTo?.({ top: 0 });
    screen = "terminal";
  }

  // ---- boot ---------------------------------------------------------------

  render();
  document.addEventListener?.("visibilitychange", () => { if (document.hidden) finishReveal(); });
  const timer = window.setInterval(async () => {
    const state = model.state;
    const remaining = Math.max(0, Math.ceil((Date.parse(state.expires_at) - Date.now()) / 1000));
    const timerNode = document.getElementById("timer");
    timerNode.textContent = TERMINAL.has(state.status) ? "—" : `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
    if (timerNode.classList) timerNode.classList.toggle("urgent", !TERMINAL.has(state.status) && remaining <= 60);
    if (TERMINAL.has(state.status)) { if (!model.reveal) window.clearInterval(timer); return; }
    if (locked() || model.holding) return;
    try {
      const latest = await api("state");
      // A slow poll must not overwrite a mutation that started meanwhile.
      if (locked() || model.holding) return;
      if (latest.revision >= model.state.revision) {
        const changed = latest.revision !== model.state.revision || latest.status !== model.state.status;
        if (changed) {
          model.previous = model.state;
          model.state = latest;
          model.confirmation = null;
          render();
        }
      }
    } catch (_) { /* Retry on the next tick; never settle in the browser. */ }
  }, 2000);
};
