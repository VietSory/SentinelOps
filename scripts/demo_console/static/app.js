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
    const state = node("p", "state", "Fault mode unavailable.");
    const match = node("p", "match", "");
    const label = action.id === "recover" ? "Reset both fault modes" : action.id === "deploy-bad-image" ? "Deploy regression image" : "Inject fault";
    const button = node("button", "", label);
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
  elements.mode.textContent = data.enabled ? "Fault injection enabled" : "Read only · Fault injection off";
  elements.mode.className = `badge ${data.enabled ? "on" : "off"}`;
  const running = data.deployment.state === "running";
  for (const {card, state, match, button, action} of cards.values()) {
    const states = action.services.map(service => [service, data.services[service]]);
    state.textContent = states.map(([service, value]) => value.ok
      ? `${service}: mode=${value.mode}, delay=${value.delay_ms} ms\nObserved at ${new Date(value.observed_at).toLocaleTimeString("en-US")}`
      : `${service}: Unavailable\n${value.error}`).join("\n\n");
    state.classList.toggle("error", states.some(([, value]) => !value.ok));
    const matches = Boolean(action.fault) && states.every(([, value]) => value.ok
      && value.mode === action.fault.mode && value.delay_ms === action.fault.delay_ms);
    card.classList.toggle("active", matches);
    match.textContent = action.id === "deploy-bad-image"
      ? "Check the deployed image separately."
      : matches ? "The observed fault mode matches this scenario." : "";
    button.disabled = pending || !data.enabled || data.busy || running
      || (action.id === "deploy-bad-image" && !data.deployment.configured);
    button.title = !data.enabled ? "Actions require DEMO_CONSOLE_ENABLE_FAULTS=true at startup."
      : running ? "Wait for the deployment script to finish."
      : action.id === "deploy-bad-image" ? data.deployment.configuration_error || "" : "";
  }
  const deployment = data.deployment;
  elements["deployment-status"].textContent = deployment.state === "running"
    ? "The script is running. Actions stay locked until it finishes."
    : deployment.state === "exited"
      ? `The script exited with code ${deployment.returncode}. Review its log and evidence.`
      : deployment.configuration_error || "The script is configured and has yet to run in this console session.";
  elements["deployment-log"].textContent = deployment.log_tail || "No log output yet.";
  const failed = Object.values(data.services).some(value => !value.ok);
  elements["refresh-status"].textContent = `${failed ? "Some fault modes are unavailable" : "Read both fault modes"} · ${new Date(data.checked_at).toLocaleTimeString("en-US")} · refreshes every 5 seconds`;
}

function unavailable(message) {
  current = null;
  elements.mode.textContent = "Console disconnected";
  elements.mode.className = "badge error";
  elements["refresh-status"].textContent = message;
  elements["deployment-status"].textContent = "Current script status is unknown.";
  elements["deployment-log"].textContent = "Current script log is unavailable.";
  for (const {card, state, match, button} of cards.values()) {
    state.textContent = "Unavailable. Current fault mode is unknown.";
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
  if (pending || !current?.enabled) return;
  if (action.id !== "recover" && !window.confirm(`${action.title}\n\nDevelopment only. This action will disrupt the service. Continue?`)) return;
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
    elements.result.textContent = `Response lost (${error.message}). The action may have taken effect. Check the current fault mode before retrying.`;
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
