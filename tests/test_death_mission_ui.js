// Run with: node --test tests/test_death_mission_ui.js
// Exercise the shipped controller with a small DOM and a controllable clock.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { randomUUID } = require("node:crypto");

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.listeners = {};
    this.disabled = false;
    this.dataset = {};
    this.textContent = "";
    this.type = "";
    this._classes = new Set();
    const styles = {};
    this.style = { setProperty(name, value) { styles[name] = value; }, get(name) { return styles[name]; } };
    const classes = this._classes;
    this.classList = {
      add: (...names) => names.forEach((n) => classes.add(n)),
      remove: (...names) => names.forEach((n) => classes.delete(n)),
      toggle: (name, force) => { (force === undefined ? !classes.has(name) : force) ? classes.add(name) : classes.delete(name); },
      contains: (name) => classes.has(name),
    };
  }
  get className() { return [...this._classes].join(" "); }
  set className(value) { this._classes = new Set(String(value).split(/\s+/).filter(Boolean)); const c = this._classes; this.classList.add = (...n) => n.forEach((x) => c.add(x)); this.classList.remove = (...n) => n.forEach((x) => c.delete(x)); this.classList.toggle = (name, force) => { (force === undefined ? !c.has(name) : force) ? c.add(name) : c.delete(name); }; this.classList.contains = (name) => c.has(name); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setPointerCapture() { this.captured = true; }
  async fire(name, event = {}) { if (!this.disabled) return this.listeners[name]?.(event); }
}

const text = (node) => [node.textContent, ...node.children.map(text)].filter(Boolean).join(" ");
const all = (node, pred, out = []) => { if (pred(node)) out.push(node); node.children.forEach((c) => all(c, pred, out)); return out; };
const find = (node, pred) => all(node, pred)[0];
const has = (node, cls) => node.classList.contains(cls);
const flush = () => new Promise((resolve) => setImmediate(resolve));

function setup(api, initial, { reducedMotion = false } = {}) {
  const nodes = new Map();
  const timeouts = new Map();
  const delays = new Map();
  const listeners = {};
  let serial = 0;
  let poll = null;
  const window = {
    setTimeout(fn, delay) { const id = ++serial; timeouts.set(id, fn); delays.set(id, delay); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    setInterval(fn) { poll = fn; return 1; },
    clearInterval() { poll = null; },
    matchMedia() { return { matches: reducedMotion }; },
  };
  const document = {
    title: "",
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    createElement(tag) { return new Element(tag); },
    addEventListener(name, fn) { listeners[name] = fn; },
  };
  vm.runInNewContext(fs.readFileSync("spy_game/webapp_static/death.js", "utf8"), {
    window, document, crypto: { randomUUID }, Set, Date, JSON, Math, String, Boolean, console,
  });
  window.startDeathMission(initial, api);
  const root = () => nodes.get("death-mission");
  const buttons = () => all(root(), (n) => n.tag === "button");
  const button = (label) => buttons().find((b) => text(b).includes(label));
  const tick = (delay) => {
    const entry = [...timeouts].find(([id]) => delays.get(id) === delay);
    assert.ok(entry, `missing ${delay}ms timer`);
    timeouts.delete(entry[0]);
    entry[1]();
  };
  return { root, buttons, button, timeouts, nodes, runPoll: () => poll && poll(), document, tick, listeners };
}

const preview = {
  status: "preview", revision: 0, expires_at: "2030-01-01T00:00:00Z",
  stake: [{ id: "informant", emoji: "🕵️", name: "Осведомитель", amount: 5 }, { id: "analyst", emoji: "🧠", name: "Аналитик", amount: 1 }],
  extraction: [{ id: "informant", emoji: "🕵️", name: "Осведомитель", amount: 2 }],
  rules: { all_in_percent: 35, multiplier: 2, seconds: 900 },
  tactics: [
    { id: "balanced", name: "Баланс", hp: 6, intel: 2, risk_modifier: 0, shield: false, locked: false, unlock: "" },
    { id: "stealth", name: "Тихий вход", hp: 5, intel: 3, risk_modifier: 0, shield: false, locked: true, unlock: "Пройти узел 3 в трёх забегах" },
  ],
};

function mission(overrides = {}) {
  return {
    version: "roguelite_v1", phase: "action", node: 1, boss_phase: 0, hp: 4, intel: 2, alarm: 5, tactic: "balanced",
    modules: ["armor"], module_names: ["Бронепластины"], checkpoint: false, outcome: null,
    max_hp: 6, max_intel: 6, raid_alarm: 6, checkpoint_node: 3, module_nodes: [1, 3], rules_summary: [],
    boss: "Бронепоезд", phase_names: ["Проникновение", "Выполнение задачи", "Отход"], title: "Патруль", odds: 31,
    events: [{ kind: "action", node: 0, boss_phase: null, title: "Архив под наблюдением", label: "Вскрыть терминал", risk: 25, failed: true, hp: -2, intel: 2, alarm: 1, raid: false, passport: false, absorbed: 0, medic: 0 }],
    log: ["Вскрыть терминал — осложнение: ❤️ −2, 🧠 +2, 🚨 +1"],
    actions: [
      { id: "bypass", label: "Обойти по разведданным", cost: 1, hp: 0, intel: 0, alarm: 0, risk: 0, damage: 2, enabled: true, odds: 28, may_die: false, certain_death: false, lethal: false,
        preview: { success: { hp: 4, intel: 1, alarm: 5, dead: false, raid: false, passport: false, absorbed: 0, medic: 0 }, failure: null } },
      { id: "rush", label: "Проскочить патруль", cost: 0, hp: 0, intel: 0, alarm: 1, risk: 50, damage: 2, enabled: true, odds: 12, may_die: true, certain_death: false, lethal: false,
        preview: { success: { hp: 3, intel: 2, alarm: 4, dead: false, raid: true, passport: false, absorbed: 1, medic: 0 }, failure: { hp: 0, intel: 2, alarm: 4, dead: true, raid: true, passport: false, absorbed: 1, medic: 0 } } },
      { id: "crawl", label: "Пройти через заграждения", cost: 0, hp: -1, intel: 0, alarm: 0, risk: 0, damage: 2, enabled: true, odds: 22, may_die: false, certain_death: false, lethal: false,
        preview: { success: { hp: 4, intel: 2, alarm: 5, dead: false, raid: false, passport: false, absorbed: 1, medic: 0 }, failure: null } },
    ],
    ...overrides,
  };
}

const run = (overrides = {}, missionOverrides = {}) => ({
  status: "in_run", revision: 3, mode: "mission", tactic: "balanced", bonus: "tier3", expires_at: "2030-01-01T00:00:00Z",
  stake: preview.stake, extraction: preview.extraction, rules: preview.rules, tactics: preview.tactics,
  mission: mission(missionOverrides), ...overrides,
});

function percentageMove(roll = 25, status = "in_run") {
  const before = run({}, { actions: mission().actions.map(a => ({ ...a, may_die: false })) });
  const check = { ...mission().events[0], action: "rush", label: "Проскочить патруль", risk: 50,
    roll, failed: roll <= 50, hp: roll <= 50 ? -2 : 0, intel: 0, alarm: -1, absorbed: 1 };
  const after = run({ revision: 4, status }, { hp: before.mission.hp + check.hp, alarm: 4,
    events: [...before.mission.events, check] });
  return { before, after, check };
}

for (const roll of [1, 50, 51, 100]) {
  test(`percentage ${roll}/100 reveals the saved branch before updating resources`, async () => {
    const { before, after, check } = percentageMove(roll);
    let writes = 0;
    const ui = setup(async (_path, options) => { if (options) writes += 1; return after; }, before);
    await ui.buttons().find(b => b.dataset.action === "rush").fire("click");
    await flush();
    assert.match(text(ui.root()), /Раскрываем исход/);
    assert.equal(text(find(ui.root(), n => has(n, "check-number"))), "… / 100");
    assert.equal(all(ui.root(), n => has(n, "check-effects")).length, 0);
    assert.equal(ui.buttons().length, 1, "only the skip control is available during disclosure");
    ui.tick(850);
    assert.equal(text(find(ui.root(), n => has(n, "check-number"))), `${roll} / 100`);
    assert.equal(text(find(ui.root(), n => has(n, "check-outcome"))), check.failed ? "Осложнение" : "Без осложнения");
    assert.match(text(ui.root()), /1–50 — осложнение; 51–100 — без осложнения/);
    assert.match(text(ui.root()), /Защита поглотила 1/);
    ui.tick(1000);
    assert.equal(text(find(find(ui.root(), n => has(n, "meter-hp")), n => has(n, "meter-value"))), `${after.mission.hp}/6`);
    assert.match(text(ui.root()), new RegExp(`Последний бросок: ${roll}/100`));
    assert.equal(all(ui.root(), n => has(n, "rolling")).length, 0);
    await ui.runPoll();
    assert.equal(writes, 1);
  });
}

test("skip and reduced motion disclose the result without waiting or another mutation", async () => {
  const { before, after } = percentageMove();
  for (const reducedMotion of [false, true]) {
    let writes = 0;
    const ui = setup(async (_path, options) => { if (options) writes += 1; return after; }, before, { reducedMotion });
    await ui.buttons().find(b => b.dataset.action === "rush").fire("click");
    await flush();
    if (!reducedMotion) await ui.button("Показать результат сразу").fire("click");
    assert.match(text(ui.root()), /Последний бросок: 25\/100/);
    assert.ok(find(ui.root(), n => has(n, "hud")));
    assert.equal(writes, 1);
    assert.equal(ui.button("Показать результат сразу"), undefined);
  }
});

test("lost response retries the original operation and reveals its persisted roll once", async () => {
  const { before, after } = percentageMove();
  const bodies = [];
  const ui = setup(async (_path, options) => {
    if (options) { bodies.push(options.body); if (bodies.length === 1) throw Error("lost response"); }
    return after;
  }, before);
  await ui.buttons().find(b => b.dataset.action === "rush").fire("click");
  await flush();
  assert.equal(find(ui.root(), n => has(n, "rolling")), undefined);
  await ui.button("Повторить запрос").fire("click");
  await flush();
  assert.deepEqual(bodies, [bodies[0], bodies[0]]);
  assert.ok(find(ui.root(), n => has(n, "rolling")));
  await ui.button("Показать результат сразу").fire("click");
  await ui.runPoll();
  assert.equal(find(ui.root(), n => has(n, "rolling")), undefined);
  const reopened = setup(async () => after, after);
  assert.match(text(reopened.root()), /Последний бросок: 25\/100/);
  assert.equal(reopened.timeouts.size, 0, "reopening never replays an animation");
});

test("a stale poll cannot interrupt a terminal reveal, and hiding the page finishes it", async () => {
  const { before, after } = percentageMove(1, "lost");
  let finishPoll;
  let first = true;
  const ui = setup(async (_path, options) => {
    if (!options && first) { first = false; return new Promise(resolve => { finishPoll = resolve; }); }
    return after;
  }, before);
  const poll = ui.runPoll();
  await ui.buttons().find(b => b.dataset.action === "rush").fire("click");
  await flush();
  finishPoll(before);
  await poll;
  assert.match(text(ui.root()), /Раскрываем исход/);
  assert.equal(ui.nodes.has("result-title"), false, "terminal outcome waits for the reveal");
  ui.document.hidden = true;
  ui.listeners.visibilitychange();
  assert.equal(ui.nodes.get("result-title").textContent, "Связь потеряна");
  assert.match(text(ui.nodes.get("death-result-check")), /1 \/ 100/);
  assert.ok(has(ui.root(), "hidden"));
});

test("newer server state suppresses an obsolete response animation", async () => {
  const { before, after, check } = percentageMove();
  const latest = run({ revision: 5 }, { events: [...after.mission.events, { ...check, roll: 90, failed: false }] });
  const ui = setup(async (_path, options) => options ? after : latest, before);
  await ui.buttons().find(b => b.dataset.action === "rush").fire("click");
  await flush();
  assert.equal(find(ui.root(), n => has(n, "rolling")), undefined);
  assert.match(text(ui.root()), /Последний бросок: 90\/100/);
});

test("all-in reveals its own saved check and then its terminal payout", async () => {
  const before = { ...preview, status: "armed", revision: 1, mode: "all_in" };
  const after = { ...before, revision: 3, status: "won", result: { returned: preview.stake, bonus: [],
    check: { kind: "all_in", label: "All-in", roll: 66, risk: 65, failed: false } } };
  const ui = setup(async () => after, before);
  await ui.button("Удерживайте").fire("pointerdown");
  ui.tick(3000);
  await flush();
  assert.match(text(ui.root()), /Раскрываем исход/);
  ui.tick(850);
  assert.equal(text(find(ui.root(), n => has(n, "check-outcome"))), "Выигрыш");
  ui.tick(1000);
  assert.equal(ui.nodes.get("result-title").textContent, "Операция выполнена");
  assert.match(text(ui.nodes.get("death-result-check")), /66 \/ 100/);
  assert.match(ui.nodes.get("result-copy").textContent, /Возвращено/);
});

test("entry keeps the whole network at stake and shows the reduced exact return", () => {
  const state = { ...preview, rules: { ...preview.rules, version: "roguelite_v4" }, victory: preview.stake };
  const ui = setup(async () => state, state);
  assert.match(text(ui.root()), /СТАВКА · ВСЯ ДОСТУПНАЯ СЕТЬ/);
  assert.match(text(ui.root()), /Личная миссия вернёт при победе: 🕵️ Осведомитель ×5/);
  assert.match(text(ui.root()), /по одному агенту за каждые пять одного типа/);
  assert.equal(ui.buttons().some(b => b.dataset.squad), false);
});

test("archive shows only legal neighbours and sends a move, not a claimed win", async () => {
  const sent = [];
  const state = run({ practice: true }, {
    phase: "challenge", room_id: "archive",
    challenge: { size: 4, path: [0], blocked: [4], goal: 15, moves_left: 8 },
    actions: [{ id: "cell_1" }, { id: "skip_puzzle" }],
  });
  const ui = setup(async (_path, options) => {
    sent.push(JSON.parse(options.body));
    return { ...state, revision: 4 };
  }, state);
  const cells = all(ui.root(), n => has(n, "archive-cell"));
  assert.equal(cells.length, 16);
  assert.equal(cells.filter(n => !n.disabled).length, 1);
  assert.equal(cells[15].disabled, true);
  assert.match(text(ui.root()), /Без потерь, наград и достижений/);
  assert.doesNotMatch(text(ui.root()), /половиной ставки/);
  await cells[1].fire("click");
  await flush();
  assert.deepEqual(sent[0].choice, { id: "cell_1" });
  await ui.button("Завершить тренировку").fire("click");
  assert.match(text(ui.root()), /Ваша сеть не изменится/);
});

test("specialist variant shares an action card and consumes only the chosen charge", async () => {
  const sent = [];
  const base = mission().actions[0];
  const state = run({}, {
    phase: "boss", boss_phase: 2,
    specialists: [{ id: "ghost_agent", name: "Агент-призрак", emoji: "👻", ability: "Стереть след", ready: true, reduction: 15 }],
    actions: [base, { ...base, id: "bypass:ghost_agent", base_id: "bypass", specialist: "ghost_agent", risk: 10, base_risk: 25 }],
  });
  const ui = setup(async (_path, options) => {
    sent.push(JSON.parse(options.body));
    return { ...state, revision: 4 };
  }, state);
  assert.equal(all(ui.root(), n => has(n, "action-card")).length, 1);
  assert.match(text(ui.root()), /25% → 10% · 1 заряд/);
  await ui.button("Стереть след").fire("click");
  await flush();
  assert.deepEqual(sent[0].choice, { id: "bypass:ghost_agent" });
  const locked = run({}, { ...state.mission, actions: state.mission.actions.map(a => ({...a, enabled:false, preview:null})) });
  const lockedUi = setup(async () => locked, locked);
  assert.equal(lockedUi.button("Стереть след").disabled,true);
  assert.match(text(lockedUi.root()), /Нужно 🧠 1/);
});

test("entry has two modes; all-in requires a separate held confirmation", async () => {
  const requests = [];
  let state = { ...preview, status: "armed", revision: 1, mode: "all_in" };
  const ui = setup(async (path, options) => {
    if (options) requests.push({ path, body: JSON.parse(options.body) });
    if (path === "death/commit") state = { status: "won", revision: 2, result: { returned: preview.stake, bonus: [] }, tactics: preview.tactics };
    return state;
  }, preview);
  assert.ok(ui.button("Пойти на миссию лично") && ui.button("All-in"));
  assert.equal(all(ui.root(), (n) => has(n, "stake-row")).length, 2);
  const lockedTactic = ui.button("Тихий вход");
  assert.ok(lockedTactic.disabled && has(lockedTactic, "locked"));
  assert.match(text(ui.root()), /Пройти узел 3 в трёх забегах/);
  await ui.button("All-in").fire("click");
  await flush();
  assert.equal(requests[0].path, "death/arm");
  assert.equal(requests[0].body.choice.mode, "all_in");
  const hold = ui.button("Удерживайте 3 секунды");
  assert.ok(hold);
  await hold.fire("pointerdown", { pointerId: 7 });
  assert.ok(hold.captured && has(hold, "holding"));
  await hold.fire("pointerup");
  assert.equal(ui.timeouts.size, 0);
  assert.equal(requests.length, 1);
  await hold.fire("pointerdown", { pointerId: 7 });
  [...ui.timeouts.values()][0]();
  await flush();
  assert.equal(requests[1].path, "death/commit");
  assert.ok(has(ui.nodes.get("death-mission"), "hidden"));
  assert.equal(ui.nodes.get("result-title").textContent, "Операция выполнена");
  assert.match(ui.nodes.get("result-copy").textContent, /Возвращено: 🕵️ Осведомитель ×5/);
});

test("personal mission preserves selected Tier 4 reward in arm request", async () => {
  let body;
  const ui = setup(async (_path, options) => {
    if (options) body = JSON.parse(options.body);
    return { ...preview, status: "armed", revision: 1, mode: "mission", bonus: "tier4" };
  }, preview);
  await ui.button("Один агент Tier 4").fire("click");
  assert.ok(has(ui.button("Один агент Tier 4"), "selected"));
  await ui.button("Пойти на миссию лично").fire("click");
  await flush();
  assert.equal(body.choice.mode, "mission");
  assert.equal(body.choice.bonus, "tier4");
  assert.equal(body.choice.tactic, "balanced");
  assert.match(text(ui.root()), /Бонус финала: Один агент Tier 4/);
});

test("lost response retries the same operation rather than creating a new wager", async () => {
  const bodies = [];
  const ui = setup(async (_path, options) => {
    if (options) {
      bodies.push(options.body);
      if (bodies.length === 1) throw Error("offline");
    }
    return { ...preview, status: "armed", revision: 1, mode: "all_in" };
  }, preview);
  await ui.button("All-in").fire("click");
  await flush();
  const retry = ui.button("Повторить запрос");
  assert.ok(retry && !retry.disabled);
  assert.ok(ui.button("Пойти на миссию лично").disabled, "other buttons stay locked while a request is pending");
  await retry.fire("click");
  await flush();
  assert.equal(bodies.length, 2);
  assert.equal(bodies[0], bodies[1]);
});

test("run screen renders HUD, route, odds, both branches and the log with deltas", () => {
  const ui = setup(async () => run(), run());
  const root = ui.root();
  const hpCells = all(find(root, (n) => has(n, "meter-hp")), (n) => has(n, "seg"));
  assert.equal(hpCells.length, 6);
  assert.equal(hpCells.filter((c) => has(c, "on")).length, 4);
  const alarmCells = all(find(root, (n) => has(n, "meter-alarm")), (n) => has(n, "seg"));
  assert.ok(has(alarmCells[5], "raid") && has(alarmCells[4], "on"));
  assert.match(text(find(root, (n) => has(n, "hud"))), /Ещё \+1 тревоги — облава/);
  const route = all(root, (n) => has(n, "route-node"));
  assert.equal(route.length, 6);
  assert.ok(has(route[1], "current") && has(route[0], "done") && has(route[5], "finale"));
  assert.match(text(route[1]), /Патруль/);
  assert.match(text(route[5]), /Бронепоезд/);
  assert.equal(find(root, (n) => has(n, "odds-value")).textContent, "31%");
  assert.match(text(find(root, (n) => has(n, "module-chips"))), /Бронепластины/);
  const cards = all(root, (n) => has(n, "action-card"));
  assert.equal(cards.length, 3);
  const rush = cards[1];
  assert.ok(has(rush, "risky"));
  assert.match(text(rush), /Осложнение 50% · базовый урон 2 · 🚨 \+1/);
  assert.match(text(rush), /без осложнения \(50%\)/);
  assert.match(text(rush), /осложнение \(50%\).*☠️ гибель группы/);
  assert.match(text(rush), /финал 12% \(−19\)/);
  const log = find(root, (n) => has(n, "log-list"));
  assert.match(text(log), /Архив под наблюдением · Вскрыть терминал/);
  assert.match(text(log), /осложнение 25%/);
  assert.match(text(log), /❤️ −2/);
  assert.match(text(log), /🧠 \+2/);
  assert.match(text(ui.root()), /Эвакуация с половиной ставки откроется после узла 3/);
});

test("dangerous actions confirm in-page with both branches instead of window.confirm", async () => {
  const sent = [];
  const ui = setup(async (path, options) => { if (options) sent.push(JSON.parse(options.body)); return run({ revision: 4 }); }, run());
  const rushButton = all(ui.root(), (n) => n.tag === "button" && n.dataset.action === "rush")[0];
  await rushButton.fire("click");
  assert.equal(sent.length, 0);
  const box = find(ui.root(), (n) => has(n, "confirm-box"));
  assert.ok(box);
  assert.match(text(box), /При осложнении группа будет уничтожена/);
  assert.match(text(box), /☠️ гибель группы/);
  await ui.button("Продолжить миссию").fire("click");
  assert.equal(find(ui.root(), (n) => has(n, "confirm-box")), undefined);
  await all(ui.root(), (n) => n.tag === "button" && n.dataset.action === "rush")[0].fire("click");
  await ui.button("Всё равно выполнить").fire("click");
  await flush();
  assert.deepEqual(sent[0].choice, { id: "rush" });
  assert.equal(sent[0].revision, 3);
});

test("extraction compares the guaranteed return with continuing", async () => {
  const sent = [];
  const ui = setup(async (path, options) => { if (options) sent.push(path); return run({ revision: 9 }); }, run({}, { checkpoint: true, node: 3 }));
  const exit = ui.button("Эвакуироваться");
  assert.match(text(exit), /вернёт 🕵️ Осведомитель ×2/);
  await exit.fire("click");
  const compare = find(ui.root(), (n) => has(n, "compare"));
  assert.match(text(compare), /ЭВАКУАЦИЯ СЕЙЧАС.*Гарантированно.*Осведомитель ×2/);
  assert.match(text(compare), /ПРОДОЛЖИТЬ.*Победа: сеть ×2/);
  assert.match(text(compare), /Шанс всего оставшегося маршрута не рассчитан/);
  assert.match(text(compare), /31% относится только к финалу/);
  await ui.button("Подтвердить эвакуацию").fire("click");
  await flush();
  assert.deepEqual(sent, ["death/extract"]);
});

test("a delayed poll cannot replace newer mutations or revive a terminal run", async () => {
  let finishPoll;
  let revision = 3;
  let firstPoll = true;
  const ui = setup(async (_path, options) => {
    if (options) {
      revision += 1;
      return revision === 5
        ? run({ revision, status: "won", result: { returned: [], bonus: [] } })
        : run({ revision }, { hp: revision });
    }
    if (firstPoll) { firstPoll = false; return new Promise((resolve) => { finishPoll = resolve; }); }
    return revision === 5
      ? run({ revision, status: "won", result: { returned: [], bonus: [] } })
      : run({ revision }, { hp: revision });
  }, run());
  const poll = ui.runPoll();
  await ui.button("Выполнить").fire("click");
  await flush();
  await ui.button("Выполнить").fire("click");
  await flush();
  finishPoll(run({ revision: 4 }, { hp: 4 }));
  await poll;
  assert.ok(has(ui.root(), "hidden"));
  assert.equal(ui.root().children.length, 0, "terminal screen leaves no stale action controls");
  assert.equal(ui.nodes.get("result-title").textContent, "Операция выполнена");
});

test("polling a new revision clears confirmation for the previous action", async () => {
  const ui = setup(async () => run({ revision: 4 }), run());
  await all(ui.root(), n => n.tag === "button" && n.dataset.action === "rush")[0].fire("click");
  assert.ok(ui.button("Всё равно выполнить"));
  await ui.runPoll();
  assert.equal(ui.button("Всё равно выполнить"), undefined);
});

test("finale extraction describes remaining phases rather than an unplayed route", async () => {
  const initial = run({}, { phase: "boss", node: 5, checkpoint: true });
  const ui = setup(async () => initial, initial);
  await ui.button("Эвакуироваться").fire("click");
  const compare = text(find(ui.root(), n => has(n, "compare")));
  assert.match(compare, /оставшиеся фазы финала.*31%/);
  assert.doesNotMatch(compare, /маршрута не рассчитан/);
});

test("new reward terms disable unavailable bonuses and arm the visible no-bonus option", async () => {
  const initial = { ...preview, bonuses: [
    { id: "none", name: "Без дополнительного агента", description: "Победа удваивает ставку.", locked: false },
    { id: "tier3", name: "Агент Tier 3 ×1", description: "Нужно 5 агентов Tier 3 или выше; сейчас 1.", locked: true },
  ] };
  let request;
  const ui = setup(async (_path, options) => {
    if (options) request = JSON.parse(options.body);
    return { ...initial, status: "armed", revision: 1, mode: "mission", bonus: "none" };
  }, initial);
  assert.ok(ui.button("Агент Tier 3 ×1").disabled);
  assert.ok(has(ui.button("Без дополнительного агента"), "selected"));
  await ui.button("Пойти на миссию лично").fire("click");
  await flush();
  assert.equal(request.choice.bonus, "none");
  assert.match(text(ui.root()), /Бонус финала: Без дополнительного агента/);
});

test("module offers show odds deltas and rooms list their options", () => {
  const offers = run({}, {
    phase: "module", node: 1, title: "Выберите модуль", odds: 45,
    actions: [
      { id: "armor", label: "Бронепластины", description: "Первый урон в комнате уменьшается на 1.", odds: 63 },
      { id: "escape", label: "Аварийный канал", description: "Эвакуация открывается после второго узла.", odds: 45 },
    ],
  });
  let ui = setup(async () => offers, offers);
  const cards = all(ui.root(), (n) => has(n, "module-card"));
  assert.equal(cards.length, 2);
  assert.match(text(cards[0]), /финал 63% \(\+18\)/);
  assert.match(text(cards[1]), /финал 45% Эвакуация/, "no delta chip when the module does not change the odds");
  const rooms = run({}, { phase: "room", title: "Маршрут", actions: [
    { id: "patrol", label: "Патруль", options: ["Обойти по разведданным", "Проскочить патруль"] },
    { id: "cache", label: "Схрон", options: ["Забрать разведданные", "Забрать аптечку"] },
  ] });
  ui = setup(async () => rooms, rooms);
  assert.match(text(ui.root()), /Узел 2: выберите комнату/);
  assert.match(text(all(ui.root(), (n) => has(n, "room-card"))[1]), /Схрон.*Забрать разведданные · Забрать аптечку/);
});

test("requests lock buttons in place and polling updates the HUD incrementally", async () => {
  let resolveRequest;
  const next = run({ revision: 4 }, { hp: 2, alarm: 4, odds: 9 });
  const ui = setup(async (path, options) => {
    if (options) return new Promise((resolve) => { resolveRequest = () => resolve(next); });
    return next;
  }, run());
  const hudBefore = find(ui.root(), (n) => has(n, "hud"));
  const bypass = all(ui.root(), (n) => n.tag === "button" && n.dataset.action === "bypass")[0];
  await bypass.fire("click");
  assert.ok(bypass.disabled, "buttons are disabled, not removed, while the request is in flight");
  assert.equal(find(ui.root(), (n) => has(n, "hud")), hudBefore, "HUD skeleton survives the busy render");
  resolveRequest();
  await flush(); await flush();
  assert.equal(find(ui.root(), (n) => has(n, "hud")), hudBefore);
  const hp = find(ui.root(), (n) => has(n, "meter-hp"));
  assert.equal(find(hp, (n) => has(n, "meter-value")).textContent, "2/6");
  const delta = find(hp, (n) => has(n, "meter-delta"));
  assert.equal(delta.textContent, "−2");
  assert.ok(has(delta, "bump") && has(delta, "down"));
  assert.ok(has(hp, "critical"));
  assert.equal(find(ui.root(), (n) => has(n, "odds-value")).textContent, "9%");
});
