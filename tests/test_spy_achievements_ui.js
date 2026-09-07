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
  addEventListener(name, fn) { this.listeners[name] = fn; }
  fire() { if (!this.disabled) this.listeners.click?.(); }
}

const flush = () => new Promise(resolve => setImmediate(resolve));

function setup() {
  const nodes = new Map();
  const calls = [];
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
    return {ok:true,json:async()=>structuredClone(state)};
  };
  vm.runInNewContext(fs.readFileSync("spy_game/webapp_static/app.js","utf8"), {
    document,fetch,window:{Telegram:{WebApp:{initData:"SIGNED",ready(){},expand(){}}},scrollTo(){}},
  });
  return {nodes,calls};
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
