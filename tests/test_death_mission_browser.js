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
s=e.initial('browser', 'balanced')
p=dict(status='preview',revision=0,expires_at='2030-01-01T00:00:00Z',
 rules=reward_rules(e.DEFAULT_VERSION),stake=[dict(id='resident',emoji='🏛',name='Резидент',amount=5)],
 extraction=[dict(id='resident',emoji='🏛',name='Резидент',amount=2)],
 bonuses=bonus_options(e.DEFAULT_VERSION,{'resident':5}),
 tactics=[dict(id=k,name=t.name,hp=t.hp,intel=t.intel,risk_modifier=t.risk_modifier,shield=t.shield,locked=k!='balanced',unlock=t.unlock) for k,t in rules.tactics.items()],
 mode='mission',tactic='balanced',bonus='tier4')
result={'preview':p,'armed':dict(p,status='armed',revision=1)}
for phase,extra in (
 ('module',dict(node=3,offers=['scanner','escape','armor'],modules=['passport'])),
 ('action',dict(node=4,room='patrol',hp=4,intel=3,alarm=4,modules=['armor','passport'])),
 ('boss',dict(node=5,boss_phase=2,hp=6,intel=6,alarm=0,modules=['armor','passport']))):
 state=dict(s,phase=phase,checkpoint=True,**extra)
 result[phase]=dict(p,status='in_run',revision=5,mission=e.public_state(state))
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
          window.startDeathMission(current, async (path, options) => {
            if (options) current = {...current, revision:current.revision+1, status:'won',result:{returned:current.stake,bonus:[]}};
            return current;
          });`;
        const html = source.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, "")
          .replace(/<link[^>]+stylesheet[^>]+>/g, "")
          .replace("</head>", `<style>${css}</style></head>`)
          .replace("</body>", `<script>${js}\n${boot}</script></body>`);
        await page.call("Page.setDocumentContent", { frameId, html });
        for (const width of [300, 320, 360, 390, 480, 760]) {
          await page.call("Emulation.setDeviceMetricsOverride", { width, height: 844, deviceScaleFactor: 1, mobile: true });
          const size = await evaluate(`({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})`);
          assert.ok(size.scroll <= size.client, `${name} at ${width}px overflows: ${JSON.stringify(size)}`);
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
