const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const dialogSource = readFileSync(join(__dirname, "../ui/connect.js"), "utf8");
const status = (state, extra = {}) => ({ ok: true, state, account: null, retry_verify: false, ...extra });
const flush = () => new Promise(resolve => setImmediate(resolve));

function createDialog(snapshots) {
  const elems = new Map();
  const events = [];
  const posts = [];
  const timers = new Map();
  let next = 0;

  function element(id) {
    if (!elems.has(id)) {
      elems.set(id, {
        dataset: {},
        handlers: {},
        add() {},
        focus() {},
        addEventListener(event, fn) { this.handlers[event] = fn; },
        getAttribute(name) { return this[name]; },
        removeAttribute(name) { delete this[name]; },
        showModal() { this.open = true; },
        close() { this.open = false; this.handlers.close(); },
        click() { this.handlers.click(); },
      });
    }
    return elems.get(id);
  }

  const window = {
    RuoApp: {
      $: element,
      async svcGet(service, path) {
        assert.equal(service, "setup");
        assert.equal(path, "/api/setup/bili/status");
        assert(snapshots.length, "Unexpected status fetch");
        return snapshots.shift();
      },
      async svcPost(service, path) {
        assert.equal(service, "setup");
        posts.push(path);
        return { ok: true };
      },
    },
    dispatchEvent(event) { events.push(event); },
  };
  runInNewContext(dialogSource, {
    window,
    Option: class {},
    CustomEvent: class {
      constructor(type, props) { this.type = type; this.detail = props.detail; }
    },
    setTimeout(fn) {
      const id = ++next;
      timers.set(id, fn);
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
  });

  return {
    element, events, posts,
    async open() { window.RuoConnect.openBili(); await flush(); },
    async poll() {
      assert.equal(timers.size, 1);
      const [id, fn] = timers.entries().next().value;
      timers.delete(id);
      fn();
      await flush();
    },
  };
}

test("older setup worker cannot issue a code to the new dialog", async () => {
  const ui = createDialog([{ ok: true, state: "ok", qr_b64: "" }]);
  await ui.open();
  assert.match(ui.element("biliScanStatus").textContent, /旧版 B 站后台/);
  assert.equal(ui.element("biliScanImage").hidden, true);
  assert.deepEqual(ui.posts, []);
  assert.deepEqual(ui.events, []);
});

test("reopened dialog retains a confirmed login that needs verification", async () => {
  const pending = status("error", { retry_verify: true, hint: "可以验证" });
  const account = { logged_in: true, uname: "回归测试" };
  const ui = createDialog([pending, pending, status("verifying"), status("ok", { account })]);
  await ui.open();
  assert.equal(ui.element("biliScanRetry").textContent, "重新验证账号");
  assert.deepEqual(ui.posts, []);
  ui.element("biliScanRetry").click();
  await flush();
  assert.deepEqual(ui.posts, ["/api/setup/bili/verify"]);
  await ui.poll();
  assert.equal(ui.element("biliScanDone").hidden, false);
  assert.equal(ui.events[0].type, "ruo:bili-connected");
  assert.equal(ui.events[0].detail, account);
});

test("compatible setup worker issues and displays a code and confirms the account", async () => {
  const account = { logged_in: true };
  const ui = createDialog([status("idle"), status("qrcode", { qr_b64: "AA==" }),
    status("scanned"), status("verifying"), status("ok", { account })]);
  await ui.open();
  assert.deepEqual(ui.posts, ["/api/setup/bili/gen"]);
  assert.equal(ui.element("biliScanImage").src, "data:image/png;base64,AA==");
  await ui.poll();
  await ui.poll();
  assert.equal(ui.element("biliScanImage").hidden, true);
  await ui.poll();
  assert.equal(ui.events[0].detail, account);
});
