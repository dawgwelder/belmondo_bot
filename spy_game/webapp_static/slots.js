(() => {
  "use strict";

  // The server settles a spin before the reels stop. A saved request ID survives
  // lost responses and app restarts; retrying it cannot debit the stake again.
  window.SpySlots = class SpySlots {
    constructor({api, reload, getState, beginMutation, endMutation, operationId}) {
      Object.assign(this, {api, reload, getState, beginMutation, endMutation, operationId});
      this.pending = null;
      this.storageKey = null;
      this.busy = false;
      this.selectedStake = 1;
      this.result = null;
      this.pendingMessage = "";
      this.$ = (id) => document.getElementById(id);
      this.$("slot-spin").addEventListener("click", () => this.spin());
    }

    text(message) { this.$("slot-result").textContent = message; }

    clearPending() {
      // Keep the ID in memory if removal fails, so a retry remains harmless.
      window.localStorage.removeItem(this.storageKey);
      this.pending = null;
    }

    showResult(spin) {
      this.result = spin;
      const symbols = this.getState().slots.symbols;
      spin.symbols.forEach((id, index) => {
        const symbol = symbols.find((entry) => entry.id === id);
        this.$(`slot-reel-${index}`).textContent = symbol?.emoji || "?";
      });
      const label = spin.net > 0 ? `Выигрыш! Прибыль +${spin.net}.`
        : spin.net === 0 ? "Пара! Ставка возвращена." : `Без совпадений. Потеря: ${spin.stake}.`;
      this.text(`${label} Ставка: ${spin.stake}. Выплата: ${spin.payout}.`);
    }

    render() {
      const state = this.getState();
      if (!state.slots) return;
      const slots = state.slots;
      if (this.storageKey !== slots.storage_key) {
        this.storageKey = slots.storage_key;
        try {
          const saved = JSON.parse(window.localStorage.getItem(this.storageKey) || "null");
          this.pending = saved && typeof saved.operation_id === "string"
            && slots.stakes.includes(saved.stake) ? saved : null;
        } catch (_) { this.pending = null; }
      }
      if (!this.busy && this.pending) {
        const settled = slots.history.find((entry) => entry.operation_id === this.pending.operation_id);
        if (settled) {
          this.showResult(settled);
          try { this.clearPending(); } catch (_) { /* a replay remains safe */ }
        }
      }
      const balance = state.agents.find((agent) => agent.id === "informant")?.amount || 0;
      this.$("slot-balance").textContent = `Доступно осведомителей: ${balance}`;
      this.$("slot-access").textContent = state.context.can_mutate
        ? "Игра доступна в любой момент, даже во время операции в чате."
        : "Для игры откройте приложение через /spy в группе с включённой разведсетью.";
      const stakes = this.$("slot-stakes");
      stakes.replaceChildren();
      slots.stakes.forEach((stake) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "small-button";
        button.textContent = `×${stake}`;
        button.setAttribute("aria-pressed", String(stake === (this.pending?.stake || this.selectedStake)));
        button.disabled = this.busy || !!this.pending;
        button.addEventListener("click", () => { this.selectedStake = stake; this.render(); });
        stakes.append(button);
      });
      const button = this.$("slot-spin");
      button.disabled = this.busy || !state.context.can_mutate || (!this.pending && balance < this.selectedStake);
      button.textContent = this.busy ? "Вращение…" : this.pending
        ? "Повторить запрос вращения" : `Крутить · ставка ×${this.selectedStake}`;
      if (!this.busy && this.pending) this.text(this.pendingMessage || "Ответ не получен. Повторите запрос: завершённое вращение не спишет ставку снова.");
      if (!this.busy && !this.result && !this.pending && slots.history.length) this.showResult(slots.history[0]);
      this.$("slot-rtp").textContent = `Средняя расчётная выплата — ${slots.rtp_percent.toLocaleString("ru-RU")}% ставки на большом числе вращений. Отдельное вращение может закончиться потерей всей ставки.`;
      const payouts = this.$("slot-payouts");
      payouts.replaceChildren();
      slots.symbols.forEach((symbol) => {
        const row = document.createElement("li");
        row.textContent = `${symbol.emoji.repeat(3)} · ${symbol.name} · ×${symbol.multiplier}`;
        payouts.append(row);
      });
      const history = this.$("slot-history");
      history.replaceChildren();
      slots.history.forEach((spin) => {
        const row = document.createElement("li");
        const icons = spin.symbols.map((id) => slots.symbols.find((s) => s.id === id)?.emoji || "?").join(" ");
        row.textContent = `${icons} · ставка ${spin.stake} → выплата ${spin.payout} · итог ${spin.net > 0 ? "+" : ""}${spin.net}`;
        history.append(row);
      });
      if (!slots.history.length) {
        const row = document.createElement("li");
        row.textContent = "Вы ещё не вращали барабаны.";
        history.append(row);
      }
    }

    async spin() {
      const state = this.getState();
      if (!state?.slots || this.busy || !state.context.can_mutate || !this.beginMutation()) return;
      this.busy = true;
      let animation;
      let requestTimeout;
      let settled = false;
      try {
        this.pendingMessage = "";
        if (!this.pending) {
          const pending = {operation_id: this.operationId(), stake: this.selectedStake};
          window.localStorage.setItem(this.storageKey, JSON.stringify(pending));
          this.pending = pending;
        }
        this.render();
        this.text("Барабаны вращаются…");
        this.$("slot-reels").classList.add("spinning");
        let frame = 0;
        if (!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
          animation = window.setInterval(() => {
            for (let index = 0; index < 3; index++) {
              this.$(`slot-reel-${index}`).textContent = state.slots.symbols[(frame + index * 2) % state.slots.symbols.length].emoji;
            }
            frame++;
          }, 90);
        }
        // Wait for the animation after settlement, including a replay. The request
        // still runs if the app is closed; its ID was persisted before sending.
        const abort = window.AbortController ? new window.AbortController() : null;
        if (abort) requestTimeout = window.setTimeout(() => abort.abort(), 15000);
        const result = await this.api("slots/spin", {
          method: "POST", body: JSON.stringify(this.pending), ...(abort ? {signal: abort.signal} : {}),
        });
        window.clearTimeout(requestTimeout);
        await new Promise((resolve) => window.setTimeout(resolve, state.slots.cooldown_seconds * 1000));
        window.clearInterval(animation);
        if (result.ok) {
          settled = true;
          this.showResult(result.spin);
          this.clearPending();
        } else if (result.status === "cooldown") {
          this.pendingMessage = "Предыдущее вращение ещё завершалось. Повторите запрос для выбранной ставки.";
          this.text(this.pendingMessage);
          // A cooldown does not settle a spin. Preserve the ID for the retry.
        } else {
          this.clearPending();
          const messages = {
            insufficient_agents: "Недостаточно осведомителей для этой ставки.",
            disabled: "Разведсеть выключена. Вращение недоступно.",
            conflict: "Параметры вращения не совпали. Обновите приложение.",
          };
          this.text(messages[result.status] || "Вращение отклонено.");
        }
        await this.reload();
      } catch (_) {
        this.text(settled ? "Вращение завершено. Не удалось обновить досье; результат сохранён."
          : this.pending ? "Ответ не получен. Повторите запрос: повторного списания не будет."
          : "Не удалось сохранить вращение. Проверьте доступ приложения к хранилищу и повторите.");
      } finally {
        window.clearTimeout(requestTimeout);
        window.clearInterval(animation);
        this.$("slot-reels").classList.remove("spinning");
        this.busy = false;
        this.endMutation();
        this.render();
      }
    }
  };
})();
