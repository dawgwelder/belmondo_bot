const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor() {
    this.children = []; this.listeners = {}; this.attributes = {}; this.disabled = false;
    this.classList = {add() {}, remove() {}};
  }
  replaceChildren() { this.children = []; }
  append(child) { this.children.push(child); }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(name, value) { this.attributes[name] = value; }
}

function setup({storage = new Map(), ledger = new Map(), failResponse = false, canMutate = true} = {}) {
  const nodes = new Map();
  const calls = [];
  let locked = false;
  let controller;
  const state = {
    context: {can_mutate: canMutate, active_event: false}, agents: [{id: "informant", amount: 10}],
    slots: {storage_key: "spy-slots:1:-100", stakes: [1,3,5], cooldown_seconds: 2, rtp_percent: 90.28,
      symbols: ["file", "key", "radio", "case", "diamond", "spy"].map((id, i) => ({id,emoji:String(i),name:id,multiplier:5})),
      history: []},
  };
  const document = {
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    createElement() { return new Element(); },
  };
  const window = {
    localStorage: {getItem: (k) => storage.get(k), setItem: (k,v) => storage.set(k,v), removeItem: (k) => storage.delete(k)},
    setInterval: () => 1, clearInterval() {}, clearTimeout() {}, setTimeout: (resolve) => queueMicrotask(resolve),
    matchMedia: () => ({matches:true}),
  };
  vm.runInNewContext(fs.readFileSync("spy_game/webapp_static/slots.js", "utf8"), {window,document});
  async function reload() {
    state.slots.history = [...ledger.values()].reverse();
    state.agents[0].amount = 10 - ledger.size;
    controller.render();
  }
  controller = new window.SpySlots({
    getState: () => state, reload,
    beginMutation: () => { if (locked) return false; locked = true; return true; },
    endMutation: () => { locked = false; }, operationId: () => `spin-${ledger.size + 1}`,
    api: async (path, options) => {
      const body = JSON.parse(options.body);
      calls.push({path,body});
      if (!ledger.has(body.operation_id)) ledger.set(body.operation_id, {
        operation_id:body.operation_id, stake:body.stake, payout:0, net:-body.stake,
        symbols:["file","key","radio"], balance_after:9,
      });
      if (failResponse) { failResponse = false; throw new Error("response lost"); }
      return {ok:true,spin:ledger.get(body.operation_id)};
    },
  });
  controller.render();
  return {controller,nodes,calls,storage,ledger,state,reload,window,setLocked: (v) => {locked = v;}};
}

test("slots are available with and without an event; spin locks duplicate clicks", async () => {
  const s = setup();
  assert.equal(s.nodes.get("slot-spin").disabled, false);
  s.state.context.active_event = true;
  s.controller.render();
  assert.equal(s.nodes.get("slot-spin").disabled, false);
  await Promise.all([s.controller.spin(), s.controller.spin()]);
  assert.equal(s.calls.length, 1);
  assert.equal(s.calls[0].path, "slots/spin");
  assert.deepEqual(s.calls[0].body, {operation_id:"spin-1",stake:1});
  assert.equal(s.storage.size, 0);
  assert.match(s.nodes.get("slot-result").textContent, /Потеря: 1/);
  assert.match(s.nodes.get("slot-balance").textContent, /9/);
  assert.equal(s.nodes.get("slot-history").children.length, 1);
});

test("lost response survives reopening and retry uses identical request", async () => {
  const first = setup({failResponse:true});
  await first.controller.spin();
  assert.equal(first.storage.size, 1);
  assert.equal(first.ledger.size, 1);
  assert.match(first.nodes.get("slot-spin").textContent, /Повторить/);
  const reopened = setup({storage:first.storage,ledger:first.ledger});
  assert.equal(reopened.nodes.get("slot-stakes").children.every((b) => b.disabled), true);
  await reopened.controller.spin();
  assert.deepEqual(reopened.calls[0].body, first.calls[0].body);
  assert.equal(reopened.ledger.size, 1);
  assert.equal(reopened.storage.size, 0);
  assert.match(reopened.nodes.get("slot-result").textContent, /Потеря: 1/);
});

test("history recovers lost result on opening without another POST", async () => {
  const first = setup({failResponse:true});
  await first.controller.spin();
  const reopened = setup({storage:first.storage,ledger:first.ledger});
  await reopened.reload();
  assert.equal(reopened.storage.size, 0);
  assert.equal(reopened.calls.length, 0);
  assert.match(reopened.nodes.get("slot-result").textContent, /Потеря: 1/);
});

test("no spending in read-only mode or during another mutation", async () => {
  const s = setup({canMutate:false});
  assert.equal(s.nodes.get("slot-spin").disabled, true);
  await s.controller.spin();
  s.state.context.can_mutate = true;
  s.setLocked(true);
  await s.controller.spin();
  assert.equal(s.calls.length, 0);
});

test("storage failure stops the request before any debit", async () => {
  const s = setup();
  s.window.localStorage.setItem = () => { throw new Error("storage blocked"); };
  await s.controller.spin();
  assert.equal(s.calls.length, 0);
  assert.match(s.nodes.get("slot-result").textContent, /хранилищу/);
});

test("stake selection and visible gross/net payout agree", async () => {
  const s = setup();
  s.nodes.get("slot-stakes").children[2].listeners.click();
  await s.controller.spin();
  assert.equal(s.calls[0].body.stake, 5);
  s.controller.showResult({stake:5,payout:200,net:195,symbols:["spy","spy","spy"]});
  assert.match(s.nodes.get("slot-result").textContent, /Прибыль \+195/);
  assert.match(s.nodes.get("slot-result").textContent, /Выплата: 200/);
  s.controller.showResult({stake:3,payout:3,net:0,symbols:["key","key","file"]});
  assert.match(s.nodes.get("slot-result").textContent, /Ставка возвращена/);
});
