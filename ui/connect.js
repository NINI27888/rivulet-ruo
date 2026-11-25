/* Shared setup dialogs for the workspace and account connections. */
(function () {
  "use strict";

  var app = window.RuoApp;
  var $ = app.$;
  var presets = [
    { id: "deepseek", name: "DeepSeek 官方", provider: "deepseek",
      base: "https://api.deepseek.com/v1", model: "deepseek-flash",
      aliases: ["https://api.deepseek.com"], displayModel: "DeepSeek V4.1 Flash",
      url: "https://platform.deepseek.com/api_keys",
      keyPage: "打开 DeepSeek 密钥页面",
      hint: "登录 DeepSeek 开放平台，在密钥页面创建 API 密钥并复制。首次使用请在平台确认 API 余额。" },
    { id: "opencode-go", name: "OpenCode Go", provider: "custom",
      base: "https://opencode.ai/inference/go/openai/v1", model: "deepseek-v4.1-flash",
      aliases: ["https://opencode.ai/inference/go/v1", "https://opencode.ai/zen/go/v1"],
      displayModel: "DeepSeek V4.1 Flash",
      url: "https://opencode.ai/console",
      keyPage: "打开 OpenCode Console",
      hint: "登录 OpenCode Console 并开通 Go。在右上角账号菜单打开 Account → Personal API keys → Add API key，选择已订阅 Go 的工作区，创建并复制密钥。" },
    { id: "qwen", name: "通义千问（阿里云百炼）", provider: "custom",
      base: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus",
      url: "https://help.aliyun.com/zh/model-studio/get-api-key",
      keyPage: "查看百炼密钥获取步骤",
      hint: "在百炼控制台的 API Key 管理页面创建并复制密钥。其他地域可在高级设置里更换接口地址。" },
    { id: "openai", name: "OpenAI 官方", provider: "openai",
      base: "https://api.openai.com/v1", model: "gpt-5.4-mini",
      url: "https://platform.openai.com/api-keys",
      keyPage: "打开 OpenAI 密钥页面",
      hint: "登录 OpenAI API 平台，在 API Keys 页面创建并复制密钥。首次使用请在平台设置 API 计费。" },
    { id: "ollama", name: "Ollama（本机）", provider: "ollama",
      base: "http://127.0.0.1:11434/v1", model: "",
      hint: "先启动电脑上的 Ollama，再输入已经下载的模型名。" },
    { id: "custom", name: "其他兼容服务 / 中转 / LM Studio", provider: "custom",
      base: "", model: "",
      hint: "在服务商或本地模型工具里复制 OpenAI 兼容接口地址、模型名和 API 密钥。" }
  ];

  var modelDialog = $("modelDialog");
  var provider = $("modelProvider");
  var key = $("modelSecret");
  var modelForm = $("modelForm");
  var fields = $("modelFields");
  var apply = $("modelApply");
  var modelState;
  var modelLoad = 0;

  presets.forEach(function (item) {
    provider.add(new Option(item.name, item.id));
  });

  function feedback(node, message, tone) {
    node.textContent = message;
    node.dataset.tone = tone || "";
    node.hidden = !message;
  }

  function presetFor(id) {
    return presets.find(function (item) { return item.id === id; });
  }

  function canonAddress(value) {
    return String(value || "").trim().replace(/\/+$/, "").replace(/\/chat\/completions$/, "").replace(/\/+$/, "");
  }

  function savedPreset(current) {
    var address = canonAddress(current.base_url);
    return presets.find(function (item) {
      return address && (item.base === address || (item.aliases || []).includes(address));
    }) || presetFor("custom");
  }

  function isLocalAddress(address) {
    try { return ["127.0.0.1", "localhost", "[::1]"].includes(new URL(address).hostname); }
    catch (e) { return false; }
  }

  function canReuseKey(address) {
    var current = modelState && modelState.current;
    return current && current.has_key && canonAddress(current.base_url) === address;
  }

  function refreshKeyHint() {
    var address = canonAddress($("modelAddress").value);
    var canReuse = canReuseKey(address);
    var local = isLocalAddress(address);
    $("modelKeyLabel").textContent = "2. 粘贴 API 密钥" + (canReuse || local ? "（可留空）" : "");
    feedback($("modelKeyHelp"), canReuse ? "留空会继续使用已保存的密钥。" :
      (local ? "如果本机服务未设置密钥，这一项可以留空。" : ""));
    key.placeholder = canReuse ? "已保存，可留空" : "从服务商复制的完整密钥";
  }

  function refreshModelSummary() {
    var item = presetFor(provider.value);
    var model = $("modelId").value.trim();
    var current = modelState.current;
    var summary = $("modelSummary");
    summary.hidden = !item || !item.model || !model ||
      !!(current && savedPreset(current) === item && current.model === model);
    summary.textContent = model ? "将测试 " + modelLabel(item, model) : "";
  }

  function modelLabel(item, model) {
    return item && item.model === model && item.displayModel ? item.displayModel : model;
  }

  function usePreset(id, current) {
    var item = presetFor(id);
    provider.value = id;
    $("modelProviderFields").hidden = !item;
    $("modelProviderHint").textContent = item ? item.hint :
      "有 API 密钥或模型服务订阅，就选对应的平台。第一次使用，可以按提示开通。DeepSeek 官方按用量计费，OpenCode Go 提供订阅。";
    $("modelAddress").value = (current && current.base_url) || (item && item.base) || "";
    $("modelId").value = (current && current.model) || (item && item.model) || "";
    if (!item) return;

    $("modelAdvanced").open = !item.base || !item.model;
    $("modelAdvancedLabel").textContent = item.model ? "高级设置 · 接口地址和模型名" : "接口地址和模型名";
    var keyPage = $("modelKeyPage");
    keyPage.hidden = !item.url;
    keyPage.textContent = item.keyPage || "";
    refreshModelSummary();
    refreshKeyHint();
  }

  function setModelBusy(busy) {
    modelState.busy = busy;
    fields.disabled = busy || modelState.finished;
    apply.disabled = busy || (!modelState.finished && !provider.value);
    $("modelDialogClose").disabled = busy;
  }

  function openModel(target, doneView) {
    if (modelDialog.open) return;
    modelState = { target: target || "workspace", doneView: doneView, current: null, finished: false };
    var own = target === "qq";
    var forQq = target === "workspace-for-qq";
    var ticket = ++modelLoad;
    $("modelDialogTitle").textContent = own ? "给 QQ 单独配置模型" : "配置聊天模型";
    $("modelDialogIntro").textContent = forQq ? "为若选一个模型，工作区和 QQ 会共用这份配置。" :
      (own ? "为若选一个模型，用于回复 QQ 消息。" : "为若选一个模型，用于生成聊天回复。");
    apply.textContent = "测试并启用";
    key.value = "";
    feedback($("modelSaved"), "");
    usePreset("");
    setModelBusy(false);
    fields.disabled = true;
    apply.disabled = true;
    feedback($("modelResult"), "正在读取已保存的配置…");
    modelDialog.showModal();

    var read = own ? app.svcGet("qq", "/api/config").then(function (r) {
      var cfg = r && r.config;
      if (cfg && cfg.model && (cfg.has_key || isLocalAddress(cfg.api_base))) {
        return { base_url: cfg.api_base, model: cfg.model, has_key: cfg.has_key };
      }
      return null;
    }) : app.svcGet("workspace", "/api/models/status");

    read.then(function (current) {
      if (ticket !== modelLoad || !modelDialog.open) return;
      if (current && current.model) {
        modelState.current = current;
        var same = savedPreset(current);
        feedback($("modelSaved"), "已保存 · " + same.name + " · " + modelLabel(same, current.model));
        usePreset(same.id, current);
      }
      feedback($("modelResult"), "");
    }).catch(function () {
      if (ticket === modelLoad && modelDialog.open) {
        feedback($("modelResult"), "暂时读不到已保存的配置，可以重试连接。", "warn");
      }
    }).finally(function () {
      if (ticket !== modelLoad || !modelDialog.open) return;
      setModelBusy(false);
      (modelState.current ? key : provider).focus();
    });
  }

  provider.addEventListener("change", function () {
    key.value = "";
    var current = modelState.current;
    usePreset(provider.value, current && savedPreset(current).id === provider.value ? current : null);
    setModelBusy(false);
    feedback($("modelResult"), "");
  });
  $("modelAddress").addEventListener("input", refreshKeyHint);
  $("modelId").addEventListener("input", refreshModelSummary);
  $("modelKeyPage").addEventListener("click", function () {
    var item = presetFor(provider.value);
    if (!item || !item.url) return;
    if (!window.__TAURI__) {
      window.open(item.url, "_blank", "noopener,noreferrer");
      return;
    }
    app.call("open_model_site", { provider: item.id }).catch(function () {
      feedback($("modelResult"), "请用浏览器打开 " + item.url, "warn");
    });
  });
  $("modelDialogClose").addEventListener("click", function () { modelDialog.close(); });
  modelDialog.addEventListener("cancel", function (event) {
    if (modelState.busy) event.preventDefault();
  });
  modelDialog.addEventListener("close", function () {
    modelLoad++;
    key.value = "";
  });

  modelForm.addEventListener("submit", function (event) {
    event.preventDefault();
    if (modelState.busy) return;
    var target = modelState.target;
    if (modelState.finished) {
      modelDialog.close();
      var next = modelState.doneView || (target === "workspace" ? "workspace" : "qq");
      app.switchView(next);
      if (next === "workspace") $("wsInput").focus();
      return;
    }

    var item = presetFor(provider.value);
    if (!item) return;
    var own = target === "qq";
    var payload = {
      provider: item.provider,
      model: $("modelId").value.trim(),
      base_url: $("modelAddress").value.trim(),
      api_key: key.value.trim()
    };
    if (!payload.base_url || !payload.model) {
      $("modelAdvanced").open = true;
      var missing = !payload.base_url ? $("modelAddress") : $("modelId");
      feedback($("modelResult"), "请先填写接口地址和模型名。", "error");
      missing.focus();
      return;
    }
    var address = canonAddress(payload.base_url);
    if (!payload.api_key && !canReuseKey(address) && !isLocalAddress(address)) {
      feedback($("modelResult"), "请按上方步骤获取 API 密钥，再粘贴到若。", "error");
      key.focus();
      return;
    }
    if (!own && modelState.current && modelState.current.id) payload.existing_id = modelState.current.id;
    setModelBusy(true);
    feedback($("modelResult"), "正在发送一句测试消息，成功后会自动启用…");
    var endpoint = own ? "/api/model/configure" : "/api/models/configure";
    app.svcPost(own ? "qq" : "workspace", endpoint, payload).then(function (result) {
      if (!result || !result.ok) throw new Error((result && result.error) || "模型暂时无法启用。");
      key.value = "";
      if (target !== "workspace-for-qq") return true;
      return app.svcPost("qq", "/api/model/use-workspace", {}).then(function (r) {
        return !!(r && r.ok);
      }).catch(function () { return false; });
    }).then(function (shared) {
      modelState.finished = true;
      feedback($("modelSaved"), "已保存 · " + item.name + " · " + modelLabel(item, payload.model));
      $("modelSummary").hidden = true;
      if (!shared) {
        feedback($("modelResult"), "工作区模型已启用。回到 QQ 页，再点「使用工作区模型」。", "warn");
      } else if (target === "workspace-for-qq") {
        feedback($("modelResult"), "测试通过。工作区和 QQ 已连接到这个模型。", "ok");
      } else if (own) {
        feedback($("modelResult"), "测试通过。QQ 已启用这个回复模型。", "ok");
      } else {
        feedback($("modelResult"), "测试通过。现在可以开始对话。", "ok");
      }
      apply.textContent = target === "workspace" && !modelState.doneView ? "开始对话" : "返回";
      window.dispatchEvent(new CustomEvent("ruo:model-configured", { detail: { target: target } }));
    }).catch(function (error) {
      var message = String(error.message || error);
      if (/服务没起来|Timeout|request error|transport/i.test(message)) {
        message = "暂时连不上本机服务。请重开若后再试。";
      }
      feedback($("modelResult"), message, "error");
    }).finally(function () {
      delete payload.api_key;
      setModelBusy(false);
      if (modelState.finished) apply.focus();
    });
  });

  var scanDialog = $("biliScanDialog");
  var scanTimer;
  var scanTicket = 0;
  var scanCanVerify = false;
  var scanImage = $("biliScanImage");
  var scanRetry = $("biliScanRetry");

  function renderScan(status, tone, qr, done) {
    feedback($("biliScanStatus"), status, tone);
    if (qr) {
      var src = "data:image/png;base64," + qr;
      if (scanImage.getAttribute("src") !== src) scanImage.src = src;
    } else {
      scanImage.removeAttribute("src");
    }
    scanImage.hidden = !qr;
    scanRetry.hidden = tone !== "error";
    scanRetry.textContent = scanCanVerify ? "重新验证账号" : "重新生成二维码";
    $("biliScanDone").hidden = !done;
  }

  function scanActive(ticket) {
    return ticket === scanTicket && scanDialog.open;
  }

  function canReadScan(result) {
    if (!result || result.ok !== true) {
      renderScan((result && result.hint) || "暂时连不上 B 站扫码服务，请重试。", "error");
      return false;
    }
    if (typeof result.retry_verify !== "boolean" || !Object.prototype.hasOwnProperty.call(result, "account")) {
      scanCanVerify = false;
      renderScan("当前窗口连到了旧版 B 站后台，完成升级后才能扫码。", "error");
      return false;
    }
    return true;
  }

  function showScanStatus(ticket, result) {
    if (result.state === "error") {
      scanCanVerify = result.retry_verify;
      renderScan(result.hint || "无法完成登录，请重试。", "error");
    } else if (result.state === "ok") {
      var account = result.account;
      if (!account || account.logged_in !== true) {
        scanCanVerify = false;
        renderScan("B 站后台未能确认账号，请重新生成二维码。", "error");
        return;
      }
      renderScan("已登录 " + (account.uname || "B 站账号") + "。", "ok", "", true);
      window.dispatchEvent(new CustomEvent("ruo:bili-connected", { detail: account }));
      $("biliScanDone").focus();
    } else if (["gen", "qrcode", "scanned", "verifying"].includes(result.state)) {
      scanCanVerify = result.state === "verifying";
      renderScan(result.hint || "正在生成二维码…", "info", result.qr_b64);
      scanTimer = setTimeout(function () { pollScan(ticket); }, 1700);
    } else {
      scanCanVerify = false;
      renderScan("扫码状态已失效，请重新生成二维码。", "error");
    }
  }

  function pollScan(ticket) {
    if (!scanActive(ticket)) return;
    app.svcGet("setup", "/api/setup/bili/status").then(function (result) {
      if (scanActive(ticket) && canReadScan(result)) showScanStatus(ticket, result);
    }).catch(function () {
      if (scanActive(ticket)) renderScan("暂时连不上扫码服务，请重试。", "error");
    });
  }

  function startScan(verify) {
    clearTimeout(scanTimer);
    scanCanVerify = !!verify;
    var ticket = ++scanTicket;
    renderScan("正在连接 B 站扫码服务…");
    app.svcGet("setup", "/api/setup/bili/status").then(function (status) {
      if (!scanActive(ticket) || !canReadScan(status)) return;
      if (verify === undefined && status.retry_verify) {
        showScanStatus(ticket, status);
        return;
      }
      renderScan(verify ? "正在验证 B 站账号…" : "正在生成二维码…");
      return app.svcPost("setup", verify ? "/api/setup/bili/verify" : "/api/setup/bili/gen", {}).then(function (result) {
        if (!scanActive(ticket)) return;
        if (result && result.ok) {
          pollScan(ticket);
        } else {
          scanCanVerify = !!(result && result.retry_verify);
          renderScan((result && result.error) || "暂时无法完成扫码登录，请重试。", "error");
        }
      });
    }).catch(function () {
      if (scanActive(ticket)) renderScan("暂时连不上扫码服务，请重试。", "error");
    });
  }

  function openBili() {
    if (scanDialog.open) return;
    scanDialog.showModal();
    startScan();
  }

  $("biliScanClose").addEventListener("click", function () { scanDialog.close(); });
  $("biliScanDone").addEventListener("click", function () { scanDialog.close(); });
  scanRetry.addEventListener("click", function () { startScan(scanCanVerify); });
  scanDialog.addEventListener("close", function () {
    scanTicket++;
    clearTimeout(scanTimer);
    scanImage.removeAttribute("src");
  });

  window.RuoConnect = { openModel: openModel, openBili: openBili };
})();
