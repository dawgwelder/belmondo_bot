(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData || "";
  let state = null;
  let mutationInFlight = false;

  const $ = (id) => document.getElementById(id);
  const notice = $("notice");

  const statusMessages = {
    insufficient_resources: "Недостаточно ресурсов для операции.",
    already_equipped: "Этот предмет уже экипирован.",
    no_free_slot: "Все слоты экипировки заняты.",
    not_owned: "Предмета больше нет в инвентаре.",
    not_equipped: "Этот слот уже пуст.",
    invalid_slot: "Слот экипировки устарел.",
    stale: "Досье изменилось. Данные обновлены.",
    max_level: "Достигнут максимальный уровень службы.",
    disabled: "Операция сейчас недоступна в этом чате.",
    invalid_recipe: "Этот контакт не проводит такую сделку."
  };

  function showNotice(message, isError = false) {
    notice.textContent = message;
    notice.classList.remove("hidden", "error");
    if (isError) notice.classList.add("error");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function hideNotice() {
    notice.classList.add("hidden");
  }

  async function api(path, options = {}) {
    const response = await fetch(`api/${path}`, {
      ...options,
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": initData,
        ...(options.headers || {})
      }
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Ошибка ${response.status}`);
    }
    return response.json();
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function empty(target, message) {
    clear(target);
    target.append(element("div", "empty", message));
  }

  function renderCosts(target, costs) {
    clear(target);
    costs.forEach((cost) => {
      target.append(element("span", "cost", `${cost.emoji} ${cost.name} ×${cost.amount}`));
    });
  }

  function row(icon, title, meta, side, modifier) {
    const wrapper = element("article", "list-row");
    if (modifier) wrapper.classList.add(modifier);
    const main = element("div", "row-main");
    main.append(element("span", "row-icon", icon));
    const labels = element("div", "row-copy");
    labels.append(element("span", "row-title", title));
    labels.append(element("span", "row-meta", meta));
    main.append(labels);
    wrapper.append(main);
    const right = element("div", "row-side");
    if (typeof side === "string") right.textContent = side;
    else if (side) right.append(side);
    wrapper.append(right);
    return wrapper;
  }

  function actionButton(label, handler) {
    const button = element("button", "small-button", label);
    button.type = "button";
    button.addEventListener("click", handler);
    return button;
  }

  function renderOverview() {
    const profile = state.profile;
    $("identity").textContent = profile.username || "Скрытый агент";
    $("total-agents").textContent = profile.total_agents;
    $("reputation").textContent = profile.reputation;
    $("agency-level").textContent = `${profile.agency_level}/${profile.agency_max_level}`;

    const context = state.context;
    $("network-light").classList.toggle("live", context.network_enabled);
    if (!context.chat_bound) {
      $("network-title").textContent = "Личное досье";
      $("network-copy").textContent = "Для операций с ресурсами откройте приложение кнопкой из группового /spy.";
    } else if (!context.network_enabled) {
      $("network-title").textContent = "Сеть не активирована";
      $("network-copy").textContent = "Попросите master включить Spy Clicker в этом чате.";
    } else {
      $("network-title").textContent = context.active_event ? "В чате идёт операция" : "Сеть активна";
      $("network-copy").textContent = `Активность ${context.activity_score.toFixed(1)} · профиль ${context.activity_profile}. События остаются в Telegram-чате.`;
    }

    renderCosts($("prestige-costs"), state.prestige.costs);
    $("prestige-button").disabled = !context.can_mutate;

    const agency = state.agency;
    $("agency-title").textContent = agency.at_cap
      ? "Максимальный уровень достигнут"
      : `Уровень ${profile.agency_level + 1}`;
    $("agency-copy").textContent = agency.at_cap
      ? `Постоянный бонус редкого результата: +${agency.rare_bonus_percent}%.`
      : `Нужно репутации: ${agency.required_reputation}. После учреждения репутация сбросится, остальные ресурсы сохранятся.`;
    renderCosts($("agency-costs"), agency.costs);
    $("agency-button").disabled = !context.can_mutate || agency.at_cap;
  }

  function renderAgents() {
    const target = $("agent-list");
    clear(target);
    if (!state.agents.length) {
      empty(target, "Сеть пока пуста. Следите за событиями в групповом чате.");
      return;
    }
    [...state.agents]
      .sort((a, b) => a.tier - b.tier || a.name.localeCompare(b.name, "ru"))
      .forEach((agent) => target.append(row(agent.emoji, agent.name, `Tier ${agent.tier}`, `×${agent.amount}`)));
  }

  function renderInventory() {
    const inventory = state.inventory;
    const target = $("inventory-list");
    const equipped = new Map(inventory.equipped.map((item) => [item.item_type, item]));
    $("slot-summary").textContent = `Инвентарь · ${inventory.equipped.length}/${inventory.slot_count} слотов`;
    clear(target);
    if (!inventory.items.length) {
      empty(target, "Инвентарь пуст. Ищите тайники разведсети.");
      return;
    }
    inventory.items.forEach((item) => {
      const active = equipped.get(item.id);
      let side = `×${item.amount}`;
      if (item.category === "equipment") {
        side = active
          ? actionButton(`Снять · ${active.slot}`, () => mutate("equipment/unequip", { slot: active.slot }, "Предмет снят."))
          : actionButton("Надеть", () => mutate("equipment/equip", { item_type: item.id }, "Предмет экипирован."));
        side.disabled = !state.context.can_mutate;
      }
      const meta = item.category === "equipment"
        ? `${active ? `Экипировано в слот ${active.slot}` : "Экипировка"} · ×${item.amount}`
        : `Расходный материал · ×${item.amount}`;
      target.append(row(item.emoji, item.name, meta, side));
    });
  }

  function renderLeaderboard() {
    const target = $("leaderboard-list");
    clear(target);
    if (!state.leaderboard.length) {
      empty(target, "Пока ни одно досье не открыто.");
      return;
    }
    state.leaderboard.forEach((entry) => {
      target.append(row(
        entry.rank <= 3 ? ["🥇", "🥈", "🥉"][entry.rank - 1] : String(entry.rank),
        entry.name,
        `Tier 3+: ${entry.rare_agents} · репутация ${entry.reputation} · служба ${entry.agency_level}`,
        String(entry.total_agents)
      ));
    });
  }

  function operationId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function rewardText(reward) {
    return `${reward.emoji} ${reward.name} ×${reward.amount}`;
  }

  function renderContacts() {
    const target = $("contact-list");
    clear(target);
    if (!state.contacts.length) {
      empty(target, "Постоянные контакты пока недоступны.");
      return;
    }
    state.contacts.forEach((contact) => {
      const button = actionButton("Обменять", () => {
        const costs = [...contact.agent_costs, ...contact.item_costs]
          .map((cost) => `${cost.emoji} ${cost.name} ×${cost.amount}`)
          .join(", ");
        confirmAction(
          `Провести обмен? Будет списано: ${costs}.`,
          () => mutate(
            "contacts/exchange",
            { recipe_id: contact.id, operation_id: operationId() },
            (result) => `Получено: ${rewardText(result.reward)}.`
          )
        );
      });
      button.disabled = !state.context.can_mutate;
      const costs = [...contact.agent_costs, ...contact.item_costs]
        .map((cost) => `${cost.emoji} ${cost.name} ×${cost.amount}`)
        .join(" · ");
      target.append(row(
        contact.reward.emoji,
        contact.name,
        `${contact.npc_name}\nСтоимость: ${costs}\nРезультат: ${rewardText(contact.reward)}`,
        button,
        "contact-row"
      ));
    });
  }

  function render() {
    renderOverview();
    renderAgents();
    renderInventory();
    renderContacts();
    renderLeaderboard();
  }

  async function reload() {
    state = await api("state");
    render();
  }

  async function mutate(path, payload, successMessage) {
    if (mutationInFlight) return;
    mutationInFlight = true;
    hideNotice();
    try {
      const result = await api(path, { method: "POST", body: JSON.stringify(payload) });
      if (!result.ok) {
        showNotice(statusMessages[result.status] || "Центр отклонил операцию.", true);
      } else {
        tg?.HapticFeedback?.notificationOccurred("success");
        showNotice(
          typeof successMessage === "function"
            ? successMessage(result)
            : successMessage
        );
      }
      await reload();
    } catch (error) {
      tg?.HapticFeedback?.notificationOccurred("error");
      showNotice(error.message, true);
    } finally {
      mutationInFlight = false;
    }
  }

  function confirmAction(message, callback) {
    if (tg?.showConfirm) tg.showConfirm(message, (confirmed) => confirmed && callback());
    else if (window.confirm(message)) callback();
  }

  $("prestige-button").addEventListener("click", () => {
    confirmAction("Списать указанных агентов и повысить репутацию?", () => {
      mutate("prestige", { expected_reputation: state.prestige.expected_reputation }, "Репутация повышена.");
    });
  });

  $("agency-button").addEventListener("click", () => {
    confirmAction("Учредить службу? Требуемые агенты будут списаны, репутация сброшена.", () => {
      mutate("agency", { expected_level: state.agency.expected_level }, "Новый уровень службы учреждён.");
    });
  });

  document.querySelectorAll(".nav-button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav-button").forEach((item) => item.classList.toggle("active", item === button));
      document.querySelectorAll(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === button.dataset.target));
      window.scrollTo({ top: 0, behavior: "auto" });
    });
  });

  async function boot() {
    tg?.ready();
    tg?.expand();
    try {
      await reload();
      $("loading").classList.add("hidden");
      $("content").classList.remove("hidden");
      $("navigation").classList.remove("hidden");
    } catch (error) {
      $("loading").classList.add("hidden");
      showNotice(error.message || "Не удалось открыть защищённый канал.", true);
    }
  }

  boot();
})();
