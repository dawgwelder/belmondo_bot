(() => {
  "use strict";

  const fragment = new URLSearchParams(window.location.hash.slice(1));
  let savedDeathToken = "";
  try { savedDeathToken = sessionStorage.getItem("spy-death-run") || ""; } catch (_) { /* Telegram can reopen the run. */ }
  const token = fragment.get("run") || savedDeathToken;
  if (token) {
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}`
    );
  }

  const $ = (id) => document.getElementById(id);
  const frequency = $("frequency");
  const lockButton = $("lock");
  const codeButton = $("try-code");
  const accuseButton = $("accuse");
  const confirmAccuseButton = $("confirm-accuse");
  let state = null;
  let round = 0;
  let locks = [];
  let digits = [];
  let finished = false;
  let timerId = null;
  let selectedSuspect = null;
  let accusationKey = null;

  function show(id) {
    document.querySelectorAll(".screen").forEach((screen) => {
      screen.classList.toggle("hidden", screen.id !== id);
      screen.classList.toggle("active", screen.id === id);
    });
  }

  async function api(path, options = {}) {
    const response = await fetch(`../api/game/${path}`, {
      ...options,
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "X-Spy-Game-Token": token,
        ...(options.headers || {})
      }
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Ошибка ${response.status}`);
    }
    return response.json();
  }

  function currentTarget() {
    return state.targets[Math.min(round, state.targets.length - 1)];
  }

  function updateSignal() {
    if (!state || state.game_type !== "intercept" || finished) return;
    const distance = Math.abs(Number(frequency.value) - currentTarget());
    const signal = Math.max(0, 100 - distance * 7);
    $("meter-fill").style.width = `${signal}%`;
    $("wave").style.setProperty("--signal", String(signal / 100));
    $("strength").textContent = signal >= 80
      ? "ЧИСТЫЙ СИГНАЛ"
      : signal >= 45
        ? "НЕСТАБИЛЬНЫЙ СИГНАЛ"
        : "ШУМ";
  }

  function prepareRound() {
    $("round").textContent = `Сигнал ${round + 1}/${state.targets.length}`;
    $("feedback").textContent = "";
    frequency.value = String(25 + ((round * 17) % 51));
    frequency.disabled = false;
    lockButton.disabled = false;
    updateSignal();
  }

  async function submitIntercept() {
    if (finished) return;
    finished = true;
    frequency.disabled = true;
    lockButton.disabled = true;
    stopTimer();
    try {
      state = await api("finish", {
        method: "POST",
        body: JSON.stringify({ locks })
      });
      renderResult();
    } catch (error) {
      renderError(error.message);
    }
  }

  function renderCodeLock() {
    const target = $("code-lock");
    target.replaceChildren();
    digits.forEach((digit, index) => {
      const wheel = document.createElement("div");
      wheel.className = "code-wheel";
      wheel.innerHTML = `
        <button type="button" data-step="1" data-index="${index}" aria-label="Увеличить цифру ${index + 1}">▲</button>
        <strong id="digit-${index}">${digit}</strong>
        <button type="button" data-step="-1" data-index="${index}" aria-label="Уменьшить цифру ${index + 1}">▼</button>
      `;
      target.appendChild(wheel);
    });
  }

  function renderAttempts() {
    $("attempts").textContent = `Проверено кодов: ${state.attempts.length}`;
    const history = $("attempt-history");
    history.replaceChildren();
    [...state.attempts].reverse().forEach((attempt) => {
      const item = document.createElement("li");
      const code = document.createElement("code");
      code.textContent = attempt.digits.join("");
      const hint = document.createElement("span");
      hint.textContent = `● ${attempt.exact}  ◐ ${attempt.misplaced}`;
      item.append(code, hint);
      history.appendChild(item);
    });
    if (state.attempts.length) {
      const last = state.attempts[state.attempts.length - 1];
      $("code-feedback").textContent = last.exact
        ? `На месте: ${last.exact}. На другой позиции: ${last.misplaced}.`
        : last.misplaced
          ? `На месте: 0. На другой позиции: ${last.misplaced}.`
          : "Ни одной цифры из этого кода.";
    }
  }

  async function submitCode() {
    if (finished || codeButton.disabled) return;
    if (state.attempts.some((attempt) =>
      attempt.digits.every((digit, index) => digit === digits[index]))) {
      $("code-feedback").textContent = "Этот код уже проверен.";
      return;
    }
    codeButton.disabled = true;
    $("code-lock").classList.add("disabled");
    try {
      state = await api("guess", {
        method: "POST",
        body: JSON.stringify({ guess: digits })
      });
      if (state.status === "ready") {
        renderAttempts();
        codeButton.disabled = false;
        $("code-lock").classList.remove("disabled");
      } else {
        finished = true;
        stopTimer();
        renderResult();
      }
    } catch (error) {
      renderError(error.message);
    }
  }

  function renderMoleCase() {
    $("mole-briefing").textContent = state.briefing;
    const clues = $("mole-clues");
    clues.replaceChildren();
    state.clues.forEach((clue) => {
      const item = document.createElement("li");
      item.textContent = clue;
      clues.appendChild(item);
    });
    const suspects = $("mole-suspects");
    suspects.replaceChildren();
    state.suspects.forEach((suspect) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "suspect-card";
      button.dataset.suspectId = suspect.id;
      const codename = document.createElement("strong");
      codename.textContent = suspect.codename;
      const role = document.createElement("span");
      role.className = "suspect-role";
      role.textContent = suspect.role;
      const dossier = document.createElement("span");
      dossier.className = "suspect-dossier";
      dossier.textContent = suspect.dossier;
      button.append(codename, role, dossier);
      suspects.appendChild(button);
    });
  }

  function chooseSuspect(suspectId) {
    selectedSuspect = state.suspects.find((suspect) => suspect.id === suspectId);
    document.querySelectorAll(".suspect-card").forEach((card) => {
      card.classList.toggle("selected", card.dataset.suspectId === suspectId);
    });
    accuseButton.disabled = !selectedSuspect;
  }

  function showAccusationConfirmation() {
    if (!selectedSuspect || finished) return;
    $("accused-name").textContent = selectedSuspect.codename;
    $("accused-copy").textContent =
      "Неверное обвинение завершит вашу личную сессию. Остальные агенты смогут продолжить расследование.";
    show("mole-confirm");
  }

  async function submitAccusation() {
    if (!selectedSuspect || finished || confirmAccuseButton.disabled) return;
    confirmAccuseButton.disabled = true;
    $("cancel-accuse").disabled = true;
    accusationKey = accusationKey || (window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : `${Date.now()}_${Math.random().toString(36).slice(2)}`);
    try {
      state = await api("mole/accuse", {
        method: "POST",
        body: JSON.stringify({
          suspect_id: selectedSuspect.id,
          revision: state.revision,
          idempotency_key: accusationKey
        })
      });
      if (state.status === "stale") {
        state = await api("state");
        confirmAccuseButton.disabled = false;
        $("cancel-accuse").disabled = false;
        show("mole-mission");
        return;
      }
      finished = true;
      stopTimer();
      renderResult();
    } catch (error) {
      confirmAccuseButton.disabled = false;
      $("cancel-accuse").disabled = false;
      $("accused-copy").textContent =
        `${error.message || "Центр не ответил."} Повторите отправку той же версии.`;
    }
  }

  function renderReward() {
    if (state.rewards) {
      $("reward").textContent = state.rewards
        .map((reward) => `${reward.emoji} ${reward.name} ×${reward.amount}`)
        .join("\n");
      return;
    }
    $("reward").textContent = state.reward
      ? `${state.reward.emoji} ${state.reward.name}${state.reward.amount
        ? ` ×${state.reward.amount}`
        : ""}`
      : "";
  }

  function renderResult() {
    show("result");
    $("result-score").textContent = state.score === null || state.score === undefined
      ? ""
      : `${state.score} очков`;
    $("reward").textContent = "";
    $("result-icon").classList.remove("danger");
    if (state.status === "won") {
      $("result-icon").textContent = "✓";
      if (state.game_type === "find_mole") {
        $("result-title").textContent = "Крот раскрыт";
        $("result-copy").textContent = "Ваша версия подтверждена Центром.";
      } else if (state.game_type === "dead_drop") {
        $("result-title").textContent = "Тайник вскрыт";
        $("result-copy").textContent = "Вы первым подобрали код.";
      } else {
        $("result-title").textContent = "Шифр раскрыт";
        $("result-copy").textContent = "Вы первым восстановили канал.";
      }
      renderReward();
    } else if (state.status === "failed") {
      $("result-icon").textContent = "×";
      $("result-icon").classList.add("danger");
      $("result-title").textContent = state.game_type === "find_mole"
        ? "Версия не подтвердилась"
        : state.game_type === "dead_drop"
          ? "Замок устоял"
          : "Недостаточно данных";
      $("result-copy").textContent = state.game_type === "find_mole"
        ? "Ваша попытка завершена, но другие агенты могут продолжить расследование."
        : state.game_type === "dead_drop"
          ? "Ваша попытка использована, но тайник ещё могут вскрыть другие агенты."
          : "Ваша попытка использована, но другие агенты ещё могут перехватить канал.";
    } else if (state.status === "expired") {
      $("result-icon").textContent = "⌛";
      $("result-title").textContent = state.game_type === "find_mole"
        ? "Дело закрыто"
        : state.game_type === "dead_drop"
          ? "Тайник изъят"
          : "Сигнал исчез";
      $("result-copy").textContent = "Время операции закончилось.";
    } else {
      $("result-icon").textContent = "◆";
      $("result-title").textContent = state.game_type === "find_mole"
        ? "Крот уже раскрыт"
        : state.game_type === "dead_drop"
          ? "Тайник уже вскрыт"
          : "Канал уже перехвачен";
      $("result-copy").textContent = "Другой агент завершил операцию раньше.";
    }
  }

  function renderError(message) {
    finished = true;
    stopTimer();
    $("error-copy").textContent = message || "Откройте игру заново из Telegram.";
    show("error");
  }

  function stopTimer() {
    if (timerId) window.clearInterval(timerId);
    timerId = null;
  }

  async function expireSession() {
    if (finished) return;
    if (state.game_type === "intercept") {
      await submitIntercept();
      return;
    }
    finished = true;
    stopTimer();
    try {
      state = await api("state");
      if (state.status === "ready") {
        finished = false;
        timerId = window.setTimeout(expireSession, 1000);
        return;
      }
      renderResult();
    } catch (error) {
      renderError(error.message);
    }
  }

  function startTimer() {
    const expiresAt = Date.parse(state.expires_at);
    const tick = () => {
      const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      if (state.game_type === "dead_drop" || state.game_type === "find_mole") {
        const minutes = Math.floor(remaining / 60);
        const seconds = String(remaining % 60).padStart(2, "0");
        $("timer").textContent = `${minutes}:${seconds}`;
      } else {
        $("timer").textContent = `${remaining}с`;
      }
      if (remaining <= 0) expireSession();
    };
    tick();
    timerId = window.setInterval(tick, 250);
  }

  lockButton.addEventListener("click", () => {
    if (finished || lockButton.disabled) return;
    const value = Number(frequency.value);
    const distance = Math.abs(value - currentTarget());
    locks.push(value);
    frequency.disabled = true;
    lockButton.disabled = true;
    $("feedback").textContent = distance <= 3
      ? "Точное совпадение"
      : distance <= 9
        ? "Фрагмент принят"
        : "Только помехи";
    round += 1;
    if (round >= state.targets.length) {
      window.setTimeout(submitIntercept, 450);
    } else {
      window.setTimeout(prepareRound, 450);
    }
  });

  $("code-lock").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-index]");
    if (!button || finished || codeButton.disabled) return;
    const index = Number(button.dataset.index);
    const step = Number(button.dataset.step);
    digits[index] = (digits[index] + step + 10) % 10;
    $(`digit-${index}`).textContent = String(digits[index]);
  });
  codeButton.addEventListener("click", submitCode);
  $("mole-suspects").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-suspect-id]");
    if (!button || finished) return;
    chooseSuspect(button.dataset.suspectId);
  });
  accuseButton.addEventListener("click", showAccusationConfirmation);
  $("cancel-accuse").addEventListener("click", () => show("mole-mission"));
  confirmAccuseButton.addEventListener("click", submitAccusation);
  frequency.addEventListener("input", updateSignal);

  async function boot() {
    if (!token) {
      renderError("Запуск не подписан. Откройте операцию из сообщения бота.");
      return;
    }
    try {
      state = await api("state");
      if (state.game_type === "death_operation") {
        try { sessionStorage.setItem("spy-death-run", token); } catch (_) { /* Storage may be disabled. */ }
        show("death-mission");
        window.startDeathMission(state, api);
        return;
      }
      if (state.status !== "ready") {
        finished = true;
        renderResult();
        return;
      }
      if (state.game_type === "find_mole") {
        document.title = "Найти крота · Spy Clicker";
        $("eyebrow").textContent = "ДЕЛО СЕКЦИИ 7";
        $("operation-title").textContent = state.title;
        renderMoleCase();
        show("mole-mission");
      } else if (state.game_type === "dead_drop") {
        document.title = "Вскрытие тайника · Spy Clicker";
        $("eyebrow").textContent = "ЗАЩИЩЁННЫЙ КОНТЕЙНЕР";
        $("operation-title").textContent = "Вскрытие тайника";
        digits = Array(state.code_length).fill(0);
        renderCodeLock();
        renderAttempts();
        show("dead-drop-mission");
      } else {
        document.title = "Перехват · Spy Clicker";
        $("eyebrow").textContent = "СЕКРЕТНЫЙ КАНАЛ";
        $("operation-title").textContent = "Перехват сигнала";
        $("prompt").textContent = state.prompt;
        $("score-hint").textContent = `Порог допуска: ${state.success_score}`;
        show("intercept-mission");
        prepareRound();
      }
      startTimer();
    } catch (error) {
      renderError(error.message);
    }
  }

  boot();
})();
