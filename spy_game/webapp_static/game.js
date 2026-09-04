(() => {
  "use strict";

  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const token = fragment.get("run") || "";
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
  let state = null;
  let round = 0;
  let locks = [];
  let finished = false;
  let timerId = null;

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
    if (!state || finished) return;
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

  async function submit() {
    if (finished) return;
    finished = true;
    frequency.disabled = true;
    lockButton.disabled = true;
    if (timerId) window.clearInterval(timerId);
    try {
      const result = await api("finish", {
        method: "POST",
        body: JSON.stringify({ locks })
      });
      renderResult(result);
    } catch (error) {
      renderError(error.message);
    }
  }

  function renderResult(result) {
    show("result");
    $("result-score").textContent = result.score === null
      ? ""
      : `${result.score} очков`;
    $("reward").textContent = "";
    if (result.status === "won") {
      $("result-icon").textContent = "✓";
      $("result-icon").classList.remove("danger");
      $("result-title").textContent = "Шифр раскрыт";
      $("result-copy").textContent = "Вы первым восстановили канал.";
      if (result.reward) {
        $("reward").textContent = `${result.reward.emoji} ${result.reward.name} ×${result.reward.amount}`;
      }
    } else if (result.status === "failed") {
      $("result-icon").textContent = "×";
      $("result-icon").classList.add("danger");
      $("result-title").textContent = "Недостаточно данных";
      $("result-copy").textContent = "Ваша попытка использована, но другие агенты ещё могут перехватить канал.";
    } else if (result.status === "expired") {
      $("result-icon").textContent = "⌛";
      $("result-title").textContent = "Сигнал исчез";
      $("result-copy").textContent = "Время операции закончилось.";
    } else {
      $("result-icon").textContent = "◆";
      $("result-title").textContent = "Канал уже перехвачен";
      $("result-copy").textContent = "Другой агент завершил операцию раньше.";
    }
  }

  function renderError(message) {
    finished = true;
    if (timerId) window.clearInterval(timerId);
    $("error-copy").textContent = message || "Откройте игру заново из Telegram.";
    show("error");
  }

  function startTimer() {
    const expiresAt = Date.parse(state.expires_at);
    const tick = () => {
      const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      $("timer").textContent = `${remaining}с`;
      if (remaining <= 0) submit();
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
      window.setTimeout(submit, 450);
    } else {
      window.setTimeout(prepareRound, 450);
    }
  });

  frequency.addEventListener("input", updateSignal);

  async function boot() {
    if (!token) {
      renderError("Запуск не подписан. Откройте Перехват из сообщения бота.");
      return;
    }
    try {
      state = await api("state");
      if (state.status !== "ready") {
        finished = true;
        renderResult(state);
        return;
      }
      $("prompt").textContent = state.prompt;
      $("score-hint").textContent = `Порог допуска: ${state.success_score}`;
      show("mission");
      prepareRound();
      startTimer();
    } catch (error) {
      renderError(error.message);
    }
  }

  boot();
})();
