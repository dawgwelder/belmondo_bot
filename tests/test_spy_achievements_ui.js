// Exercise the shipped Mini App controller with a minimal DOM and signed API boundary.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.listeners = {}; this.disabled = false;
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  get firstChild() { return this.children[0]; }
  removeChild(child) { this.children = this.children.filter((item) => item !== child); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  fire() { if (!this.disabled) this.listeners.click?.(); }
}

const flush = () => new Promise(resolve => setImmediate(resolve));

function setup(inventory, context, startParam) {
  const nodes = new Map();
  const calls = [];
  const storage = new Map();
  const localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key)
  };
  const entries = [
    {id:"director",name:"Серый кардинал",description:"Получить директора.",unlocked:true,is_new:true,progress:1,target:1,points:50,note:"Центр отметил заслуги."},
    {id:"comeback",name:"Засекречено",description:"Условия скрыты.",unlocked:false,is_new:false,progress:null,target:null,points:50,note:null}
  ];
  const state = {
    profile: {username:"@bond",total_agents:1,reputation:0,agency_level:0,agency_max_level:5},
    context: {chat_bound:false,network_enabled:false,can_mutate:false},
    agents:[],inventory:{items:[],equipped:[],slot_count:3},contacts:[],leaderboard:[],
    prestige:{costs:[]},agency:{costs:[],at_cap:false,rare_bonus_percent:0,required_reputation:3},
    achievements:{entries,total:2,unlocked:1,points:50,new_count:1,title_id:null,title:null}
  };
  if (inventory) state.inventory = inventory;
  if (context) state.context = context;
  const document = {
    getElementById(id) { if (!nodes.has(id)) nodes.set(id,new Element()); return nodes.get(id); },
    createElement(tag) { return new Element(tag); }, querySelectorAll() { return []; }
  };
  const fetch = async (path, options) => {
    calls.push({path,options});
    if (options.method === "POST") {
      const body = JSON.parse(options.body);
      if (path.endsWith("title")) {
        state.achievements.title_id = body.achievement_id;
        state.achievements.title = body.achievement_id ? "Серый кардинал" : null;
      } else {
        entries[0].is_new = false; state.achievements.new_count = 0;
      }
      return {ok:true,json:async()=>({ok:true})};
    }
    // Mirror the backend: the selected chat follows the X-Spy-Chat header.
    const chosen = options.headers["X-Spy-Chat"];
    const snapshot = structuredClone(state);
    if (chosen && snapshot.context.chats) {
      snapshot.context.chats.forEach((chat) => { chat.selected = chat.handle === chosen; });
    }
    return {ok:true,json:async()=>snapshot};
  };
  const webApp = {initData:"SIGNED",initDataUnsafe:{start_param:startParam},ready(){},expand(){}};
  vm.runInNewContext(fs.readFileSync("spy_game/webapp_static/app.js","utf8"), {
    document,fetch,window:{Telegram:{WebApp:webApp},scrollTo(){},localStorage},
  });
  return {nodes,calls,storage};
}

test("archive hides secrets, selects owned title and serializes repeated clicks",async()=>{
  const {nodes,calls} = setup();
  await flush();
  const cards = nodes.get("achievement-list").children;
  assert.equal(cards.length,2);
  assert.equal(cards[1].children[0].children[1].children[0].textContent,"Засекречено");
  assert.equal(cards[1].children[1].children.length,0);
  const titleButton = cards[0].children[1].children[0];
  titleButton.fire(); titleButton.fire();
  await flush();
  const posts = calls.filter(c=>c.options.method === "POST");
  assert.equal(posts.length,1);
  assert.deepEqual(JSON.parse(posts[0].options.body),{achievement_id:"director"});
  assert.equal(posts[0].options.headers["X-Telegram-Init-Data"],"SIGNED");
  assert.equal(nodes.get("identity").textContent,"@bond · Серый кардинал");
});

test("seen acknowledges only displayed new achievements and clear removes title",async()=>{
  const {nodes,calls} = setup();
  await flush();
  nodes.get("achievements-seen").fire();
  await flush();
  const seen = calls.find(c=>c.path.endsWith("seen"));
  assert.deepEqual(JSON.parse(seen.options.body),{achievement_ids:["director"]});
  assert.equal(nodes.get("achievements-seen").disabled,true);
  nodes.get("achievement-list").children[0].children[1].children[0].fire();
  await flush();
  nodes.get("title-clear").fire();
  await flush();
  assert.equal(nodes.get("identity").textContent,"@bond");
});


test("no membership explains how to unlock operations without a /spy link",async()=>{
  const {nodes} = setup(null,{chat_bound:false,network_enabled:false,can_mutate:false,chats:[]});
  await flush();
  assert.equal(nodes.get("network-title").textContent,"Личное досье");
  assert.match(nodes.get("network-copy").textContent,/Напишите в игровом чате или вызовите \/spy/);
  assert.equal(nodes.get("chat-switcher").children.length,0);
  assert.equal(nodes.get("prestige-button").disabled,true);
});

test("chat switcher lists member chats and sends the opaque handle",async()=>{
  const chats = [
    {handle:"aaaaaaaaaaaaaaaa",title:"Fresh Network",selected:true},
    {handle:"bbbbbbbbbbbbbbbb",title:"Old Network",selected:false}
  ];
  const {nodes,calls,storage} = setup(null,{
    chat_bound:true,network_enabled:true,can_mutate:true,active_event:false,
    activity_score:3.5,activity_profile:"balanced",chats
  });
  await flush();
  const select = nodes.get("chat-switcher");
  assert.deepEqual(select.children.map(o=>[o.value,o.textContent,o.selected]),[
    ["aaaaaaaaaaaaaaaa","Fresh Network",true],["bbbbbbbbbbbbbbbb","Old Network",false]
  ]);
  assert.equal(nodes.get("prestige-button").disabled,false);
  assert.equal(storage.get("spy-app:chat"),"aaaaaaaaaaaaaaaa");
  select.listeners.change({target:{value:"bbbbbbbbbbbbbbbb"}});
  await flush();
  const last = calls[calls.length-1];
  assert.equal(last.path,"api/state");
  assert.equal(last.options.headers["X-Spy-Chat"],"bbbbbbbbbbbbbbbb");
  assert.equal(select.children.find(o=>o.selected).value,"bbbbbbbbbbbbbbbb");
  assert.equal(storage.get("spy-app:chat"),"bbbbbbbbbbbbbbbb");
  for (const call of calls) assert.doesNotMatch(JSON.stringify(call), /-100/);
});

test("a /spy launch hint discards the remembered chat for the first load",async()=>{
  const chats = [{handle:"aaaaaaaaaaaaaaaa",title:"A",selected:true},{handle:"bbbbbbbbbbbbbbbb",title:"B",selected:false}];
  const {calls} = setup(null,{chat_bound:true,network_enabled:true,can_mutate:true,active_event:false,activity_score:1,activity_profile:"balanced",chats},"hint-token");
  await flush();
  assert.equal(calls[0].options.headers["X-Spy-Chat"],undefined);
});

test("inventory distinguishes worn charges and fresh trade copies",async()=>{
  const {nodes} = setup({slot_count:3,equipped:[{item_type:"wiretap",slot:1}],items:[
    {id:"wiretap",name:"Прослушка",emoji:"W",category:"equipment",amount:2,exchangeable_amount:1,
     uses_remaining:4,max_uses:5,effect:"Шанс 20% получить бонус."}
  ]});
  await flush();
  const card = nodes.get("inventory-list").children[0];
  const labels = card.children[0].children[1];
  assert.match(labels.children[1].textContent,/4\/5 срабатываний/);
  assert.match(labels.children[1].textContent,/для обмена ×1/);
  assert.match(labels.children[1].textContent,/нельзя обменять/);
  assert.match(card.children[1].children[0].textContent,/Снять/);
});
