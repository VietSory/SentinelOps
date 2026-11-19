"use strict";

const elements = Object.fromEntries([
  "cards", "mode", "refresh", "refresh-status", "result", "deployment-status", "deployment-log",
].map(id => [id, document.getElementById(id)]));
const cards = new Map();
let current = null;
let pending = false;
let polling = false;
let generation = 0;

function node(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

function buildCards(actions) {
  for (const action of actions) {
    if (cards.has(action.id)) continue;
    const card = node("article", "card", "");
    if (action.id === "recover") card.classList.add("recovery");
    const state = node("p", "state", "Chưa đọc được trạng thái.");
    const match = node("p", "match", "");
    const button = node("button", "", action.id === "recover" ? "Khôi phục cả hai" : action.id === "deploy-bad-image" ? "Deploy image hỏng" : "Bơm lỗi");
    button.type = "button";
    button.disabled = true;
    button.setAttribute("aria-label", action.title);
    button.addEventListener("click", () => apply(action));
    card.append(node("h2", "", action.title), node("p", "description", action.description), state, match, button);
    elements.cards.append(card);
    cards.set(action.id, {card, state, match, button, action});
  }
  elements.cards.setAttribute("aria-busy", "false");
}

function render(data) {
  current = data;
  buildCards(data.actions);
  elements.mode.textContent = data.enabled ? "Bơm lỗi ĐANG BẬT" : "CHỈ ĐỌC · Bơm lỗi TẮT";
  elements.mode.className = `badge ${data.enabled ? "on" : "off"}`;
  const running = data.deployment.state === "running";
  for (const {card, state, match, button, action} of cards.values()) {
    const states = action.services.map(service => [service, data.services[service]]);
    state.textContent = states.map(([service, value]) => value.ok
      ? `${service}: mode=${value.mode}, delay=${value.delay_ms} ms\nGET lúc ${new Date(value.observed_at).toLocaleTimeString("vi-VN")}`
      : `${service}: KHÔNG ĐỌC ĐƯỢC\n${value.error}`).join("\n\n");
    state.classList.toggle("error", states.some(([, value]) => !value.ok));
    const matches = Boolean(action.fault) && states.every(([, value]) => value.ok
      && value.mode === action.fault.mode && value.delay_ms === action.fault.delay_ms);
    card.classList.toggle("active", matches);
    match.textContent = action.id === "deploy-bad-image"
      ? "Trạng thái image: chưa xác minh qua GET faults."
      : matches ? "GET đang khớp cấu hình thẻ này." : "";
    button.disabled = pending || !data.enabled || data.busy || running
      || (action.id === "deploy-bad-image" && !data.deployment.configured);
    button.title = !data.enabled ? "Cần DEMO_CONSOLE_ENABLE_FAULTS=true khi khởi động."
      : running ? "Đợi script deploy hoàn tất."
      : action.id === "deploy-bad-image" ? data.deployment.configuration_error || "" : "";
  }
  const deployment = data.deployment;
  elements["deployment-status"].textContent = deployment.state === "running"
    ? "Script đang chạy. Các thao tác khác tạm khóa; chờ script xử lý khôi phục."
    : deployment.state === "exited"
      ? `Script đã thoát, mã ${deployment.returncode}. Kiểm tra log và bằng chứng của script.`
      : deployment.configuration_error || "Script đã cấu hình; chưa chạy trong phiên console này.";
  elements["deployment-log"].textContent = deployment.log_tail || "Chưa có log.";
  const failed = Object.values(data.services).some(value => !value.ok);
  elements["refresh-status"].textContent = `${failed ? "Có service không đọc được" : "Đã đọc cả hai service"} · ${new Date(data.checked_at).toLocaleTimeString("vi-VN")} · chu kỳ 5 giây`;
}

function unavailable(message) {
  current = null;
  elements.mode.textContent = "MẤT KẾT NỐI CONSOLE";
  elements.mode.className = "badge off";
  elements["refresh-status"].textContent = message;
  elements["deployment-status"].textContent = "Không đọc được tiến trình; trạng thái hiện tại chưa biết.";
  elements["deployment-log"].textContent = "Không đọc được log hiện tại.";
  for (const {card, state, match, button} of cards.values()) {
    state.textContent = "KHÔNG ĐỌC ĐƯỢC — trạng thái hiện tại chưa biết.";
    state.classList.add("error");
    card.classList.remove("active");
    match.textContent = "";
    button.disabled = true;
  }
}

async function refresh() {
  if (polling) return;
  polling = true;
  const started = generation;
  try {
    const response = await fetch("/api/status", {cache: "no-store", signal: AbortSignal.timeout(12000)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (started === generation) render(data);
  } catch (error) {
    if (started === generation) unavailable(`Không kết nối được console: ${error.message}. Kiểm tra backend localhost.`);
  } finally {
    polling = false;
  }
}

async function apply(action) {
  if (pending || !current?.enabled) return;
  if (action.id !== "recover" && !window.confirm(`${action.title}\n\nCHỈ DÀNH CHO DEV. Thao tác này làm service hỏng thật. Tiếp tục?`)) return;
  pending = true;
  generation += 1;
  render(current);
  elements.result.className = "";
  elements.result.textContent = `Đang gửi: ${action.title}…`;
  try {
    const response = await fetch(`/api/actions/${action.id}`, {
      method: "POST", headers: {"X-Console-Action": "apply"},
      signal: AbortSignal.timeout(30000),
    });
    const data = await response.json();
    const details = data.results ? Object.entries(data.results).map(([service, value]) =>
      `${service}: ${value.acknowledged ? "POST được chấp nhận" : value.error}`).join("\n") : "";
    elements.result.textContent = [data.message || data.detail || `HTTP ${response.status}`, details].filter(Boolean).join("\n");
    elements.result.className = response.ok ? "" : "error";
  } catch (error) {
    elements.result.className = "error";
    elements.result.textContent = `Mất phản hồi: ${error.message}. Thao tác có thể đã áp dụng. Đọc lại trạng thái; không tự gửi lại.`;
  } finally {
    pending = false;
    generation += 1;
    // Keep buttons disabled until the next fresh observation, even after POST 200.
    current = null;
    for (const {button} of cards.values()) button.disabled = true;
    await refresh();
  }
}

elements.refresh.addEventListener("click", refresh);
setInterval(refresh, 5000);
refresh();
