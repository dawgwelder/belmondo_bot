// Optional real-browser regression check, no npm packages or live bot required.
// CHROME_BINARY="/path/to/chrome" node --test tests/test_death_mission_browser.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { spawn, execFileSync } = require("node:child_process");
const repo = path.resolve(__dirname, "..");

async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let serial = 0;
  const waiting = new Map();
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (!message.id) return;
    const pending = waiting.get(message.id);
    if (!pending) return;
    waiting.delete(message.id);
    message.error ? pending.reject(Error(JSON.stringify(message.error))) : pending.resolve(message.result);
  };
  return {
    call(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = ++serial;
        waiting.set(id, { resolve, reject });
        socket.send(JSON.stringify({ id, method, params }));
      });
    },
    close() { socket.close(); },
  };
}

function fixtures() {
  const python = process.env.PYTHON || path.join(repo, ".venv/bin/python");
  return JSON.parse(execFileSync(python, ["-c", `
import json
from spy_game import death_mission as e
from spy_game.death_mission_rewards import bonus_options
from spy_game.death_mission_simulation import reward_rules
rules=e.rules(e.DEFAULT_VERSION)
s=e.initial('browser', 'balanced', specialists=('saboteur','ghost_agent'))
p=dict(status='preview',revision=0,expires_at='2030-01-01T00:00:00Z',
 rules=reward_rules(e.DEFAULT_VERSION),stake=[dict(id='resident',emoji='🏛',name='Резидент',amount=8)],
 extraction=[dict(id='resident',emoji='🏛',name='Резидент',amount=4)],
 victory=[dict(id='resident',emoji='🏛',name='Резидент',amount=9)],
 bonuses=bonus_options(e.DEFAULT_VERSION,{'resident':8}),
 tactics=[dict(id=k,name=t.name,hp=t.hp,intel=t.intel,risk_modifier=t.risk_modifier,shield=t.shield,locked=k!='balanced',unlock=t.unlock) for k,t in rules.tactics.items()],
 mode='mission',tactic='balanced',bonus='tier4')
result={'preview':p,'armed':dict(p,status='armed',revision=1)}
result['room']=dict(p,status='in_run',revision=2,mission=e.public_state(s))
s['route'][0]=['archive','patrol']
puzzle,_=e.advance(s,'archive','browser')
result['challenge']=dict(p,status='in_run',revision=3,mission=e.public_state(puzzle))
result['practice']=dict(result['challenge'],practice=True,stake=[],extraction=[],victory=[])
for phase,extra in (
 ('module',dict(node=3,offers=['scanner','escape','armor'],modules=['passport'])),
 ('action',dict(node=4,room='patrol',hp=4,intel=3,alarm=4,modules=['armor','passport'])),
 ('boss',dict(node=5,boss_phase=2,hp=6,intel=6,alarm=0,modules=['armor','passport']))):
 state=dict(s,phase=phase,checkpoint=True,**extra)
 result[phase]=dict(p,status='in_run',revision=5,mission=e.public_state(state))
result['boss_locked']=dict(p,status='in_run',revision=6,mission=e.public_state(dict(state,intel=0)))
for name, raw in (('percent_failure', 0), ('percent_success', 99), ('percent_terminal', 0), ('percent_reduced', 24)):
 state=dict(s,phase='action',room='patrol',node=0,hp=4,intel=2,alarm=0,modules=['armor'],log=[])
 action='rush'
 if name=='percent_terminal':
  state.update(phase='boss',node=5,boss_phase=2,hp=1,intel=0,modules=[])
  action='force'
 original=e.roll
 e.roll=lambda seed,key,*args,**kwargs: raw if key.startswith('action:') else original(seed,key,*args,**kwargs)
 after,_=e.advance(state,action,'browser-percent')
 e.roll=original
 response=dict(p,status=after['outcome'] or 'in_run',revision=6,mission=e.public_state(after),result={})
 result[name]=dict(p,status='in_run',revision=5,mission=e.public_state(state),_after=response,_action=action)
print(json.dumps(result))
`], { cwd: repo, encoding: "utf8" }));
}

test("Death Mission fits narrow screens and hides the completed run in real CSS", {
  skip: !process.env.CHROME_BINARY,
  timeout: 45000,
}, async () => {
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), "death-browser-"));
  const chrome = spawn(process.env.CHROME_BINARY, [
    "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", "--disable-component-update", "--disable-sync",
    `--user-data-dir=${profile}`, "--remote-debugging-port=0", "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  let browser;
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error("Chrome debugging endpoint did not start")), 15000);
      let stderr = "";
      chrome.once("error", error => { clearTimeout(timer); reject(error); });
      chrome.stderr.on("data", chunk => {
        stderr += chunk;
        const match = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
        if (match) { clearTimeout(timer); resolve(match[1]); }
      });
      chrome.once("exit", code => { clearTimeout(timer); reject(Error(`Chrome exited ${code}: ${stderr}`)); });
    });
    browser = await connect(url);
    const host = new URL(url).host;
    const [source, css, js] = await Promise.all([
      fs.readFile(path.join(repo, "spy_game/webapp_static/game.html"), "utf8"),
      fs.readFile(path.join(repo, "spy_game/webapp_static/game.css"), "utf8"),
      fs.readFile(path.join(repo, "spy_game/webapp_static/death.js"), "utf8"),
    ]);
    for (const [name, initial] of Object.entries(fixtures())) {
      const target = await (await fetch(`http://${host}/json/new?about:blank`, { method: "PUT" })).json();
      const page = await connect(target.webSocketDebuggerUrl);
      const evaluate = async expression => {
        const result = await page.call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
        assert.equal(result.exceptionDetails, undefined, JSON.stringify(result.exceptionDetails));
        return result.result.value;
      };
      try {
        await page.call("Page.enable");
        const frameId = (await page.call("Page.getFrameTree")).frameTree.frame.id;
        const boot = `
          crypto.randomUUID ||= () => 'browser-test';
          document.querySelectorAll('.screen').forEach(s => s.classList.toggle('hidden', s.id !== 'death-mission'));
          let current = ${JSON.stringify(initial)};
          const next = current._after;
          if (next) {
            const originalTimeout = window.setTimeout.bind(window);
            const originalClear = window.clearTimeout.bind(window);
            const timers = new Map();
            let serial = 0;
            window.setTimeout = (fn, delay) => {
              if (![850, 1000].includes(delay)) return originalTimeout(fn, delay);
              const id = --serial; timers.set(id, fn); return id;
            };
            window.clearTimeout = id => { timers.delete(id); originalClear(id); };
            window.advanceReveal = () => { const [id, fn] = timers.entries().next().value; timers.delete(id); fn(); };
          }
          window.startDeathMission(current, async (path, options) => {
            if (options) current = next || {...current, revision:current.revision+1, status:'won',result:{returned:current.stake,bonus:[]}};
            return current;
          });`;
        const html = source.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, "")
          .replace(/<link[^>]+stylesheet[^>]+>/g, "")
          .replace("</head>", `<style>${css}</style></head>`)
          .replace("</body>", `<script>${js}\n${boot}</script></body>`);
        if (name === "percent_reduced") await page.call("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
        await page.call("Page.setDocumentContent", { frameId, html });
        if (initial._after) {
          await evaluate(`(async () => {
            document.querySelector('button[data-action="${initial._action}"]').click();
            const confirm = [...document.querySelectorAll('button')].find(b => b.textContent === 'Всё равно выполнить');
            if (confirm) confirm.click();
            await new Promise(resolve => setTimeout(resolve, 0));
          })()`);
          assert.equal(await evaluate(`!!document.querySelector('.percent-check.rolling')`), name !== "percent_reduced");
        }
        for (const width of [300, 320, 360, 390, 480, 760]) {
          await page.call("Emulation.setDeviceMetricsOverride", { width, height: 844, deviceScaleFactor: 1, mobile: true });
          const size = await evaluate(`({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,overflow:[...document.querySelectorAll('#death-mission *')].filter(e=>e.getBoundingClientRect().right>document.documentElement.clientWidth).map(e=>({tag:e.tagName,cls:e.className,text:e.textContent.slice(0,100),right:e.getBoundingClientRect().right}))})`);
          assert.ok(size.scroll <= size.client, `${name} at ${width}px overflows: ${JSON.stringify(size)}`);
          if (width === 390 && process.env.DEATH_SCREENSHOT_DIR && ["preview", "room", "challenge", "boss"].includes(name)) {
            await fs.mkdir(process.env.DEATH_SCREENSHOT_DIR, { recursive: true });
            const shot = await page.call("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
            await fs.writeFile(path.join(process.env.DEATH_SCREENSHOT_DIR, `${name}.png`), Buffer.from(shot.data, "base64"));
          }
        }
        if (initial._after) {
          if (name !== "percent_reduced") {
            await evaluate(`window.advanceReveal()`);
            const disclosure = await evaluate(`({number:document.querySelector('.check-number').textContent,focus:document.activeElement.textContent})`);
            assert.equal(disclosure.number, `${initial._after.mission.events.at(-1).roll} / 100`);
            assert.equal(disclosure.focus, "Продолжить");
          }
          for (const width of [300, 390, 760]) {
            await page.call("Emulation.setDeviceMetricsOverride", { width, height: 844, deviceScaleFactor: 1, mobile: true });
            assert.equal(await evaluate(`document.documentElement.scrollWidth <= document.documentElement.clientWidth`), true, `${name} resolved overflow at ${width}`);
            if (width === 390 && process.env.DEATH_SCREENSHOT_DIR) {
              await fs.mkdir(process.env.DEATH_SCREENSHOT_DIR, { recursive: true });
              const shot = await page.call("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
              await fs.writeFile(path.join(process.env.DEATH_SCREENSHOT_DIR, `${name}.png`), Buffer.from(shot.data, "base64"));
            }
          }
          if (name !== "percent_reduced") await evaluate(`window.advanceReveal()`);
          if (name === "percent_terminal") {
            assert.equal(await evaluate(`getComputedStyle(document.getElementById('death-mission')).display`), "none");
            assert.equal(await evaluate(`document.getElementById('result-title').textContent`), "Связь потеряна");
            assert.equal(await evaluate(`document.querySelector('#death-result-check .check-number').textContent`), "1 / 100");
          } else {
            assert.equal(await evaluate(`!!document.querySelector('.last-check[open] .check-number')`), true);
          }
          await page.call("Emulation.setDeviceMetricsOverride", { width: 300, height: 844, deviceScaleFactor: 1, mobile: true });
          assert.equal(await evaluate(`document.documentElement.scrollWidth <= document.documentElement.clientWidth`), true, `${name} completed overflow`);
        }
        if (name === "room") {
          const nodes = await evaluate(`({current:document.querySelectorAll('.map-layer.current button').length,hiddenButtons:document.querySelectorAll('.map-layer.hidden button').length})`);
          assert.equal(nodes.current, 2);
          assert.equal(nodes.hiddenButtons, 0);
        }
        if (name === "challenge" || name === "practice") {
          const board = await evaluate(`({cells:document.querySelectorAll('.archive-cell').length,allowed:document.querySelectorAll('.archive-cell:not(:disabled)').length,skip:[...document.querySelectorAll('button')].some(b=>b.textContent.includes('Пропустить схему'))})`);
          assert.equal(board.cells, 16);
          assert.ok(board.allowed > 0 && board.allowed <= 4);
          assert.equal(board.skip, true);
        }
        if (name === "action") {
          await evaluate(`document.querySelector('button[data-action="extract"]').click()`);
          await page.call("Emulation.setDeviceMetricsOverride", { width: 300, height: 844, deviceScaleFactor: 1, mobile: true });
          const size = await evaluate(`({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})`);
          assert.ok(size.scroll <= size.client, `extraction comparison overflows: ${JSON.stringify(size)}`);
        }
        if (name === "boss") {
          await evaluate(`document.querySelector('button[data-action="plan"]').click()`);
          const result = await evaluate(`({death:getComputedStyle(document.getElementById('death-mission')).display,result:getComputedStyle(document.getElementById('result')).display,title:document.getElementById('result-title').textContent})`);
          assert.equal(result.death, "none");
          assert.notEqual(result.result, "none");
          assert.equal(result.title, "Операция выполнена");
        }
        if (name === "boss_locked") {
          const buttons = await evaluate(`({locked:document.querySelector('button[data-action="plan:ghost_agent"]')?.disabled,usable:document.querySelector('button[data-action="sewer:ghost_agent"]')?.disabled})`);
          assert.equal(buttons.locked,true);
          assert.equal(buttons.usable,false);
        }
      } finally {
        page.close();
        await browser.call("Target.closeTarget", { targetId: target.id });
      }
    }
  } finally {
    if (browser) {
      await browser.call("Browser.close").catch(() => {});
      browser.close();
    }
    if (chrome.exitCode === null) {
      chrome.kill("SIGTERM");
      await new Promise(resolve => chrome.once("exit", resolve));
    }
    await fs.rm(profile, { recursive: true, force: true });
  }
});
