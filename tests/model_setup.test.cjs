const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const source = readFileSync(join(__dirname, "../ui/connect.js"), "utf8");
const goBase = "https://opencode.ai/inference/go/openai/v1";
const goModel = "deepseek-v4.1-flash";
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup(current = { ok: true, enabled: false }, { tauri = false, target = "workspace" } = {}) {
  const elements = new Map();
  const posts = [];
  const opened = [];
  const commands = [];
  let focused;

  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        dataset: {},
        handlers: {},
        options: [],
        value: "",
        add(option) { this.options.push(option); },
        addEventListener(event, handler) { this.handlers[event] = handler; },
        focus() { focused = id; },
        showModal() { this.open = true; },
      });
    }
    return elements.get(id);
  }

  const window = {
    __TAURI__: tauri,
    RuoApp: {
      $: element,
      async svcGet(service, path) {
        assert.equal(service, target);
        assert.equal(path, target === "qq" ? "/api/config" : "/api/models/status");
        return current;
      },
      async svcPost(service, path, body) {
        posts.push({ service, path, body: { ...body } });
        return { ok: true };
      },
      async call(command, args) { commands.push({ command, provider: args.provider }); },
    },
    dispatchEvent() {},
    open(url) { opened.push(url); },
  };
  runInNewContext(source, {
    window,
    URL,
    Option: class {
      constructor(name, value) { this.name = name; this.value = value; }
    },
    CustomEvent: class {},
  });

  return {
    element, posts, opened, commands,
    focus: () => focused,
    async open() { window.RuoConnect.openModel(target); await flush(); },
    select(id) {
      const picker = element("modelProvider");
      picker.value = id;
      picker.handlers.change();
    },
    async submit() {
      element("modelForm").handlers.submit({ preventDefault() {} });
      await flush();
    },
  };
}

test("first-time model setup asks which platform supplies the key", async () => {
  const ui = setup();
  await ui.open();
  assert.equal(ui.element("modelProvider").value, "");
  assert.equal(ui.element("modelProviderFields").hidden, true);
  assert.equal(ui.element("modelApply").disabled, true);
  assert.equal(ui.focus(), "modelProvider");
  assert(ui.element("modelProvider").options.some(option => option.value === "opencode-go"));

  ui.select("deepseek");
  assert.equal(ui.element("modelAddress").value, "https://api.deepseek.com/v1");
  assert.equal(ui.element("modelId").value, "deepseek-flash");
  ui.element("modelKeyPage").handlers.click();
  assert.deepEqual(ui.opened, ["https://platform.deepseek.com/api_keys"]);

  ui.element("modelSecret").value = "example-deepseek-key";
  ui.select("opencode-go");
  assert.equal(ui.element("modelSecret").value, "");
  assert.match(ui.element("modelProviderHint").textContent, /Personal API keys/);
  assert.equal(ui.element("modelAdvanced").open, false);
  assert.equal(ui.element("modelSummary").textContent, "将测试 DeepSeek V4.1 Flash");

  await ui.submit();
  assert.equal(ui.focus(), "modelSecret");
  assert.equal(ui.posts.length, 0);
  ui.element("modelSecret").value = "example-go-key";
  await ui.submit();
  assert.deepEqual(ui.posts, [{
    service: "workspace", path: "/api/models/configure",
    body: { provider: "custom", base_url: goBase, model: goModel, api_key: "example-go-key" },
  }]);
  assert.equal(ui.element("modelSaved").textContent, "已保存 · OpenCode Go · DeepSeek V4.1 Flash");
});

test("reopening prior Go settings preserves the endpoint, model, and saved key", async () => {
  const saved = {
    id: "mdl_example", provider: "custom", base_url: "https://opencode.ai/zen/go/v1",
    model: "deepseek-v4-pro", has_key: true,
  };
  const ui = setup(saved, { tauri: true });
  await ui.open();
  assert.equal(ui.element("modelProvider").value, "opencode-go");
  assert.match(ui.element("modelSaved").textContent, /OpenCode Go · deepseek-v4-pro/);
  assert.match(ui.element("modelKeyHelp").textContent, /继续使用已保存的密钥/);
  ui.element("modelKeyPage").handlers.click();
  assert.deepEqual(ui.commands, [{ command: "open_model_site", provider: "opencode-go" }]);

  ui.select("deepseek");
  ui.select("opencode-go");
  assert.equal(ui.element("modelAddress").value, saved.base_url);
  assert.equal(ui.element("modelId").value, saved.model);
  await ui.submit();
  assert.deepEqual(ui.posts[0].body, {
    provider: "custom", base_url: saved.base_url, model: saved.model, api_key: "", existing_id: saved.id,
  });
});

test("custom compatible providers can still enter their endpoint, model, and key", async () => {
  const ui = setup();
  await ui.open();
  ui.select("custom");
  assert.equal(ui.element("modelAdvanced").open, true);
  assert.equal(ui.element("modelKeyPage").hidden, true);
  ui.element("modelAddress").value = "https://gateway.example.com/v1";
  ui.element("modelId").value = "vendor/chat-model";
  ui.element("modelSecret").value = "example-custom-key";
  await ui.submit();
  assert.deepEqual(ui.posts[0].body, {
    provider: "custom", base_url: "https://gateway.example.com/v1", model: "vendor/chat-model",
    api_key: "example-custom-key",
  });
});

test("QQ keeps a previously saved local model with no API key", async () => {
  const local = { api_base: "http://[::1]:11434/v1", model: "local-model", has_key: false };
  const ui = setup({ config: local }, { target: "qq" });
  await ui.open();
  assert.match(ui.element("modelSaved").textContent, /local-model/);
  assert.match(ui.element("modelKeyLabel").textContent, /可留空/);
  await ui.submit();
  assert.deepEqual(ui.posts, [{
    service: "qq", path: "/api/model/configure",
    body: { provider: "custom", base_url: local.api_base, model: local.model, api_key: "" },
  }]);
});
