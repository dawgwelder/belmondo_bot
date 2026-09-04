"use strict";

window.startDeathMission = (initial, api) => {
  let state = initial;
  let pending = null;
  let busy = false;
  let holding = null;
  let confirmation = null;
  let tactic = "balanced";
  let bonus = "tier3";
  const root = document.getElementById("death-mission");
  const terminal = new Set(["won", "lost", "extracted", "timed_out", "cancelled_refunded", "expired", "lost_race"]);
  const errors = {
    STALE_STAKE: "Состав изменился. Проверьте ставку и подтвердите заново.",
    STALE_REVISION: "Другой экран изменил состояние. Данные обновлены.",
    RUN_IN_PROGRESS: "Вы уже на операции в другом чате.",
    INSUFFICIENT_AGENTS: "Нет доступных агентов для ставки.",
    CONFIRMATION_EXPIRED: "Подтверждение истекло. Выберите режим заново.",
    INVALID_ACTION: "Действие недоступно.",
    ALREADY_FINISHED: "Операция завершена.",
  };
  document.title = "Смертельная операция · Spy Clicker";
  document.getElementById("operation-title").textContent = "Смертельная операция";
  document.getElementById("eyebrow").textContent = "ЦЕНТР СПЕЦОПЕРАЦИЙ";

  function element(tag, text, className) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    root.append(node);
    return node;
  }

  function button(label, handler, secondary = false) {
    const node = element("button", label, secondary ? "secondary-button" : "action-button");
    node.type = "button";
    node.disabled = busy || Boolean(pending);
    node.addEventListener("click", handler);
    return node;
  }

  function select(label, values, selected, change) {
    const wrapper = element("label", label, "death-select");
    const node = document.createElement("select");
    values.forEach(([id, name]) => {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = name;
      node.append(option);
    });
    node.value = selected;
    node.disabled = busy || Boolean(pending);
    node.addEventListener("change", () => change(node.value));
    wrapper.append(node);
  }

  async function send(action, choice = {}) {
    if (busy || pending) return;
    pending = { action, body: { revision: state.revision, operation_id: crypto.randomUUID(), choice } };
    await retry();
  }

  async function retry() {
    if (busy || !pending) return;
    busy = true;
    render();
    try {
      const result = await api(`death/${pending.action}`, { method: "POST", body: JSON.stringify(pending.body) });
      state = result;
      pending = null;
      confirmation = null;
      // A replay can return an older revision. Always recover the latest view.
      try { state = await api("state"); } catch (_) { /* Saved response remains usable. */ }
      busy = false;
      render();
      if (result.error) element("p", errors[result.error] || "Состояние изменилось. Проверьте экран.", "feedback");
    } catch (_) {
      busy = false;
      render();
      element("p", "Ответ не получен. Повторите тот же запрос, чтобы узнать результат.", "feedback");
      const retryButton = button("Повторить запрос", retry);
      retryButton.disabled = false;
    }
  }

  function holdButton() {
    const node = button("Удерживайте 3 секунды, чтобы поставить сеть", () => {});
    const cancel = () => {
      if (holding) window.clearTimeout(holding);
      holding = null;
      node.textContent = "Удерживайте 3 секунды, чтобы поставить сеть";
    };
    const start = () => {
      if (holding || busy || pending) return;
      node.textContent = "Подтверждение ставки… удерживайте";
      holding = window.setTimeout(() => {
        holding = null;
        send("commit");
      }, 3000);
    };
    node.addEventListener("pointerdown", start);
    ["pointerup", "pointerleave", "pointercancel", "blur"].forEach((event) => node.addEventListener(event, cancel));
    node.addEventListener("keydown", (event) => {
      if (event.key === " " || event.key === "Enter") { event.preventDefault(); start(); }
    });
    node.addEventListener("keyup", cancel);
  }

  function render() {
    root.replaceChildren();
    element("p", state.text || "Операция недоступна. Откройте её из Telegram.", "death-copy");
    if (terminal.has(state.status)) {
      if (state.tactics) element("p", "Открытые тактики: " + state.tactics.map(t => t.name).join(", "));
      return;
    }
    if (state.status === "preview") {
      select("Тактика личной миссии", state.tactics.map(t => [t.id, t.name]), tactic, v => { tactic = v; });
      select("Бонус за полное прохождение", [["tier3", "Два агента Tier 3"], ["tier4", "Один агент Tier 4"]], bonus, v => { bonus = v; });
      button("🎲 All-in — без личного прохождения", () => send("arm", { mode: "all_in", tactic: "balanced", bonus: "tier3" }));
      button("🕵️ Пойти на миссию лично", () => send("arm", { mode: "mission", tactic, bonus }));
    } else if (state.status === "armed") {
      holdButton();
      button("Назад к выбору", () => send("back"), true);
    } else if (state.status === "in_run") {
      if (confirmation) {
        element("p", confirmation === "extract" ? "Завершить миссию и вернуть указанный состав?" : "Сдаться и потерять всю ставку?");
        button("Подтвердить", () => send(confirmation));
        button("Продолжить миссию", () => { confirmation = null; render(); }, true);
        return;
      }
      state.mission.actions.forEach(action => {
        const node = button(action.label, () => {
          if (action.lethal && !window.confirm("Это действие может уничтожить группу. Продолжить?")) return;
          send("action", { id: action.id });
        });
        node.disabled ||= action.enabled === false;
      });
      const extraction = state.mission.checkpoint;
      button(extraction ? "Эвакуироваться" : "Сдаться", () => {
        confirmation = extraction ? "extract" : "abandon";
        render();
      }, true);
    }
  }

  render();
  const timer = window.setInterval(async () => {
    const remaining = Math.max(0, Math.ceil((Date.parse(state.expires_at) - Date.now()) / 1000));
    document.getElementById("timer").textContent = terminal.has(state.status) ? "—" :
      `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
    if (terminal.has(state.status)) { window.clearInterval(timer); return; }
    if (busy || pending || holding) return;
    try {
      const latest = await api("state");
      // A slow poll must not overwrite a mutation that started meanwhile.
      if (busy || pending || holding) return;
      if (latest.revision >= state.revision) {
        const changed = latest.revision !== state.revision || latest.status !== state.status;
        state = latest;
        if (changed) render();
      }
    } catch (_) { /* Retry on the next tick; never settle in the browser. */ }
  }, 2000);
};
