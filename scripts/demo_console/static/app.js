"use strict";

const elements = Object.fromEntries([
  "cards", "mode", "refresh", "refresh-status", "result", "deployment-status", "oom-status",
  "terminal-status", "terminal-output",
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

function clock(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleTimeString("en-US");
}

const SCRIPT_ACTIONS = {"deploy-bad-image": "deployment", "oom": "oom", "payment-gradual-fault": "gradual_fault",
  "deploy-gradual-image": "gradual_image", "reset-demo": "reset"};
const SCRIPT_LABELS = {deployment: "Image deployment", oom: "Isolated OOM", gradual_fault: "Gradual latency fault",
  gradual_image: "Payment v2 release", reset: "Demo reset"};

function scriptFor(action, data) {
  const key = SCRIPT_ACTIONS[action.id];
  return key ? (data.scripts?.[key] ?? data[key] ?? null) : null;
}

function runnerBusy(data) {
  return Object.values(data.scripts ?? {deployment: data.deployment, oom: data.oom}).some(runner => runner?.state === "running");
}

function blockReason(action, data) {
  if (!data.enabled) return "Actions require DEMO_CONSOLE_ENABLE_FAULTS=true at startup.";
  if (pending || data.busy || runnerBusy(data)) return "Wait for the current action or script to finish.";
  if (action.available !== true) return action.unavailable_reason || "Scenario readiness is unavailable.";
  const runner = scriptFor(action, data);
  if (action.id in SCRIPT_ACTIONS && !runner?.configured) {
    return runner?.configuration_error || "The scenario runner is not configured.";
  }
  return "";
}

function buildCards(actions) {
  for (const action of actions) {
    if (cards.has(action.id)) {
      cards.get(action.id).action = action;
      continue;
    }
    const card = node("article", "card", "");
    card.dataset.actionId = action.id;
    if (action.id === "recover") card.classList.add("recovery");
    const state = node("p", "state", "Current state unavailable.");
    const match = node("p", "match", "");
    const label = action.id === "recover" ? "Reset both fault modes"
      : action.id === "deploy-bad-image" ? "Deploy regression image"
      : action.id === "oom" ? "Trigger isolated OOM" : "Inject fault";
    const button = node("button", "", label);
    button.type = "button";
    button.disabled = true;
    button.setAttribute("aria-label", action.title);
    button.addEventListener("click", () => apply(cards.get(action.id).action));
    card.append(node("h2", "", action.title), node("p", "description", action.description), state, match, button);
    elements.cards.append(card);
    cards.set(action.id, {card, state, match, button, action});
  }
  elements.cards.setAttribute("aria-busy", "false");
}

function describeRunner(name, runner) {
  if (!runner) return `${name}: current runner status is unavailable.`;
  if (runner.state === "running") return `${name}: the script is running. All actions stay locked until it finishes.`;
  if (runner.state === "exited") return `${name}: the script exited with code ${runner.returncode}. Review its output and recovery evidence.`;
  return `${name}: ${runner.configuration_error || "configured; no script has run in this console session."}`;
}

function renderTerminal(data) {
  const terminal = data.terminal;
  if (!terminal || !Array.isArray(terminal.entries)) {
    elements["terminal-status"].textContent = "Execution journal unavailable from this server.";
    elements["terminal-status"].className = "terminal-status error";
    return;
  }
  const lines = [];
  for (const entry of terminal.entries.slice(-200)) {
    const prefix = `#${entry.sequence} ${clock(entry.at)} · ${entry.action_id} · ${entry.state}`;
    lines.push(`[${prefix}]`);
    if (entry.command) lines.push(`$ ${entry.command}`);
    if (entry.detail) lines.push(String(entry.detail));
    lines.push("");
  }
  for (const [key, label] of Object.entries(SCRIPT_LABELS)) {
    const log = terminal.logs?.[key];
    if (log) lines.push(`── ${label} · latest script output ──`, String(log), "");
  }
  const output = elements["terminal-output"];
  const follow = output.scrollHeight - output.scrollTop - output.clientHeight < 40;
  const text = lines.join("\n") || "No commands have run in this console session. Choose an available scenario to see its commands and output here.";
  if (output.textContent !== text) {
    output.textContent = text;
    if (follow) output.scrollTop = output.scrollHeight;
  }
  elements["terminal-status"].textContent = `${runnerBusy(data) || data.busy ? "Running" : "Ready"} · ${terminal.entries.length} journal entries · observed ${clock(data.checked_at)}`;
  elements["terminal-status"].className = "terminal-status";
}

function render(data) {
  buildCards(data.actions);
  current = data;
  elements.mode.textContent = data.enabled ? "Fault injection enabled" : "Read only · Fault injection off";
  elements.mode.className = `badge ${data.enabled ? "on" : "off"}`;
  const currentIds = new Set(data.actions.map(action => action.id));
  for (const {card, state, match, button, action} of cards.values()) {
    if (!currentIds.has(action.id)) {
      button.disabled = true;
      state.textContent = "This scenario is no longer available from the server.";
      state.classList.add("error");
      card.classList.remove("active");
      match.textContent = "";
      continue;
    }
    const states = action.services.map(service => [service, data.services[service]]);
    const runner = scriptFor(action, data);
    state.textContent = action.id === "oom"
      ? describeRunner("Isolated memory workload", runner)
      : ["reset-demo", "deploy-gradual-image"].includes(action.id)
      ? describeRunner(SCRIPT_LABELS[SCRIPT_ACTIONS[action.id]], runner)
      : states.map(([service, value]) => value?.ok
        ? `${service}: mode=${value.mode}, delay=${value.delay_ms} ms\nObserved at ${clock(value.observed_at)}`
        : `${service}: Unavailable\n${value?.error || "Current fault mode is unknown."}`).join("\n\n");
    state.classList.toggle("error", ["oom", "reset-demo", "deploy-gradual-image"].includes(action.id) ? !runner?.configured : states.some(([, value]) => !value?.ok));
    const matches = Boolean(action.fault) && states.length > 0 && states.every(([, value]) => value?.ok
      && value.mode === action.fault.mode && value.delay_ms === action.fault.delay_ms);
    card.classList.toggle("active", matches);
    const reason = blockReason(action, data);
    match.textContent = reason || (action.id === "oom"
      ? "Dedicated OOM workload only. Review and approve recovery in the Operator UI."
      : action.id === "deploy-bad-image" ? "Check the deployed image and recovery evidence separately."
      : matches ? "The observed fault mode matches this scenario." : "Ready to run.");
    button.disabled = Boolean(reason);
    button.title = reason;
  }
  elements["deployment-status"].textContent = describeRunner("Image deployment", data.deployment);
  elements["oom-status"].textContent = describeRunner("Isolated OOM", data.oom);
  renderTerminal(data);
  const failed = Object.values(data.services).some(value => !value.ok);
  elements["refresh-status"].textContent = `${failed ? "Some fault modes are unavailable" : "Read both fault modes"} · ${clock(data.checked_at)} · refreshes every 5 seconds`;
}

function unavailable(message) {
  current = null;
  elements.mode.textContent = "Console disconnected";
  elements.mode.className = "badge error";
  elements["refresh-status"].textContent = message;
  elements["deployment-status"].textContent = "Image deployment: current script status is unknown.";
  elements["oom-status"].textContent = "Isolated OOM: current script status is unknown.";
  elements["terminal-status"].textContent = "Disconnected · output below is the last observed snapshot.";
  elements["terminal-status"].className = "terminal-status error";
  for (const {card, state, match, button} of cards.values()) {
    state.textContent = "Unavailable. Current scenario state is unknown.";
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
    if (started === generation) unavailable(`Cannot reach the console. Check the local server or port-forward. (${error.message})`);
  } finally {
    polling = false;
  }
}

async function apply(action) {
  if (!current || blockReason(action, current)) return;
  const impact = action.id === "oom"
    ? "This starts the isolated OOM scenario. Recovery requires review and approval in the Operator UI."
    : "Development only. This action will disrupt the service.";
  if (action.id !== "recover" && !window.confirm(`${action.title}\n\n${impact}\nContinue?`)) return;
  pending = true;
  generation += 1;
  render(current);
  elements.result.className = "";
  elements.result.textContent = `Sending ${action.title}…`;
  try {
    const response = await fetch(`/api/actions/${action.id}`, {
      method: "POST", headers: {"X-Console-Action": "apply"},
      signal: AbortSignal.timeout(30000),
    });
    const data = await response.json();
    const details = data.results ? Object.entries(data.results).map(([service, value]) =>
      `${service}: ${value.acknowledged ? "Request accepted" : value.error}`).join("\n") : "";
    elements.result.textContent = [data.message || data.detail || `HTTP ${response.status}`, details].filter(Boolean).join("\n");
    elements.result.className = response.ok ? "" : "error";
  } catch (error) {
    elements.result.className = "error";
    elements.result.textContent = `Response lost (${error.message}). The action may have taken effect. Check the current scenario state and execution output before retrying.`;
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
