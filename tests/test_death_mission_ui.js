// Run with: node --test tests/test_death_mission_ui.js
// Exercise the shipped controller with a small DOM and a controllable clock.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { randomUUID } = require("node:crypto");

class Element {
  constructor(tag = "div") { this.tag = tag; this.children = []; this.listeners = {}; this.disabled = false; }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  async fire(name, event = {}) { if (!this.disabled) return this.listeners[name]?.(event); }
}

function setup(api) {
  const nodes = new Map();
  const timeouts = new Map();
  let serial = 0;
  const window = {
    setTimeout(fn) { const id = ++serial; timeouts.set(id, fn); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    setInterval() { return 1; }, clearInterval() {}, confirm() { return true; },
  };
  const document = {
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    createElement(tag) { return new Element(tag); },
  };
  vm.runInNewContext(fs.readFileSync("spy_game/webapp_static/death.js", "utf8"), {
    window, document, crypto: { randomUUID }, Set, Date,
  });
  window.startDeathMission({ status: "preview", revision: 0, text: "Выбор", tactics: [{ id: "balanced", name: "Баланс" }] }, api);
  const buttons = () => nodes.get("death-mission").children.filter(n => n.tag === "button");
  return { buttons, timeouts, nodes };
}

const flush = () => new Promise(resolve => setImmediate(resolve));

test("entry has two modes; all-in requires a separate held confirmation", async () => {
  const requests = [];
  let state = { status: "armed", revision: 1, text: "Подтверждение" };
  const ui = setup(async (path, options) => {
    if (options) requests.push({ path, body: JSON.parse(options.body) });
    if (path === "death/commit") state = { status: "won", revision: 2, text: "Готово" };
    return state;
  });
  assert.equal(ui.buttons().length, 2);
  await ui.buttons()[0].fire("click");
  await flush();
  assert.equal(requests[0].path, "death/arm");
  assert.equal(requests[0].body.choice.mode, "all_in");
  const hold = ui.buttons()[0];
  await hold.fire("pointerdown");
  await hold.fire("pointerup");
  assert.equal(ui.timeouts.size, 0);
  assert.equal(requests.length, 1);
  await hold.fire("pointerdown");
  [...ui.timeouts.values()][0]();
  await flush();
  assert.equal(requests[1].path, "death/commit");
  assert.equal(ui.buttons().length, 0);
});

test("personal mission preserves selected Tier 4 reward in arm request", async () => {
  let body;
  const ui = setup(async (_path, options) => {
    if (options) body = JSON.parse(options.body);
    return { status: "armed", revision: 1, text: "Подтверждение" };
  });
  const selects = ui.nodes.get("death-mission").children.filter(n => n.tag === "label");
  const reward = selects[1].children[0];
  reward.value = "tier4";
  await reward.fire("change");
  await ui.buttons()[1].fire("click");
  await flush();
  assert.equal(body.choice.mode, "mission");
  assert.equal(body.choice.bonus, "tier4");
});

test("lost response retries the same operation rather than creating a new wager", async () => {
  const bodies = [];
  const ui = setup(async (_path, options) => {
    if (options) {
      bodies.push(options.body);
      if (bodies.length === 1) throw Error("offline");
    }
    return { status: "armed", revision: 1, text: "Подтверждение" };
  });
  await ui.buttons()[0].fire("click");
  await flush();
  const retry = ui.buttons().find(b => b.textContent === "Повторить запрос");
  assert.ok(retry);
  await retry.fire("click");
  await flush();
  assert.equal(bodies.length, 2);
  assert.equal(bodies[0], bodies[1]);
});
