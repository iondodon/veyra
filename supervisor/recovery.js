"use strict";
const q = selector => document.querySelector(selector);
const dialog = q("#confirmation");
let preview = null;
let submitting = false;
let automatic = location.pathname === "/";

async function request(path, payload) {
  const response = await fetch(path, {cache: "no-store", ...(payload ? {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  } : {})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Veyra could not complete this request.");
  return result;
}

async function poll() {
  try {
    const state = await request("/api/recovery");
    q("#agent-status").textContent = state.busy ? "Recovery in progress" : state.ready ? "Agent online" : "Agent offline";
    q("#agent-status").classList.toggle("online", state.ready);
    q("#title").textContent = state.busy ? "Veyra is restarting" : state.ready ?
      (state.error ? "Veyra is using a working version" : "Veyra is running") :
      state.error || state.changes.length ? "Veyra couldn’t start" : "Veyra is starting";
    q("#description").textContent = state.changes.length ? "Uncommitted agent code must be committed or discarded before startup." :
      state.ready ? "Your chat is available. Recovery controls remain here if you need them." :
      "This page stays available while the agent starts or restarts.";
    q("#error").hidden = !state.error;
    q("#error").textContent = state.error || "";
    q("#revision").textContent = state.revision ? state.revision.slice(0, 12) : "Unavailable";
    q("#last-working").textContent = state.last_working ? state.last_working.slice(0, 12) : "Not recorded yet";
    q("#open-chat").hidden = !state.ready;
    q("#retry").disabled = state.busy || submitting;
    q("#discard").disabled = state.busy || submitting || !state.changes.length;
    q("#restore").disabled = state.busy || submitting || state.changes.length > 0 ||
      !state.last_working || state.last_working === state.revision;
    q("#change-summary").textContent = state.changes.length ?
      state.changes.length + " uncommitted agent file(s). Discarding resets only agent code." : "";
    q("#diagnostics").textContent = state.diagnostics.join("\n") || "No diagnostics yet.";
    if (automatic && state.ready && !state.error && !dialog.open) location.replace("/");
  } catch (error) {
    q("#feedback").textContent = error.message;
  } finally { window.setTimeout(poll, 1000); }
}

async function openPreview(action) {
  automatic = false;
  q("#feedback").textContent = "";
  try {
    preview = await request("/api/recovery/preview", {action});
    const discard = action === "discard";
    q("#confirmation-title").textContent = discard ? "Discard code changes?" : "Return to a working version?";
    q("#confirmation-description").textContent = discard ?
      "Reset agent code to the current commit, then retry startup? The active agent will be restarted." :
      "Restore the last working agent code in a new commit, then run its startup check?";
    q("#confirmation-revision").textContent = (discard ? preview.revision : preview.target).slice(0, 12);
    q("#changes").replaceChildren();
    for (const change of preview.changes) {
      const item = document.createElement("li");
      const path = document.createElement("code");
      path.textContent = change.path;
      const status = document.createElement("span");
      status.textContent = change.status === "??" || change.status.includes("A") ? "Delete new file" :
        change.status.includes("D") ? "Restore deleted file" : "Revert changes";
      item.append(path, status);
      q("#changes").append(item);
    }
    q("#warning").textContent = discard ? "These code changes will be permanently lost." :
      "Branch history and changes outside agent/ are kept. The agent will restart.";
    q("#consent-label").hidden = !discard;
    q("#consent").checked = false;
    q("#confirmation-error").textContent = "";
    q("#confirm").textContent = discard ? "Discard changes and retry" : "Restore version and retry";
    q("#confirm").className = discard ? "danger" : "primary";
    q("#confirm").disabled = discard;
    dialog.showModal();
  } catch (error) { q("#feedback").textContent = error.message; }
}
q("#discard").addEventListener("click", () => openPreview("discard"));
q("#restore").addEventListener("click", () => openPreview("restore"));
q("#consent").addEventListener("change", () => { q("#confirm").disabled = !q("#consent").checked; });
q("#cancel").addEventListener("click", () => dialog.close());
dialog.addEventListener("close", () => { preview = null; q("#consent").checked = false; });
dialog.addEventListener("cancel", event => { if (submitting) event.preventDefault(); });
q("#confirmation-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (submitting || !preview || (preview.action === "discard" && !q("#consent").checked)) return;
  submitting = true;
  q("#confirm").disabled = true;
  q("#cancel").disabled = true;
  try {
    await request("/api/recovery/action", {action: preview.action, token: preview.token, confirmed: true});
    dialog.close();
    q("#feedback").textContent = "Recovery requested. Waiting for the startup check…";
  } catch (error) {
    q("#confirmation-error").textContent = error.message + " Close this dialog and open a fresh preview.";
    preview = null;
  } finally { submitting = false; q("#cancel").disabled = false; }
});
q("#retry").addEventListener("click", async () => {
  automatic = false;
  q("#retry").disabled = true;
  try {
    await request("/api/recovery/action", {action: "retry"});
    q("#feedback").textContent = "Retry requested. Waiting for the startup check…";
  } catch (error) { q("#feedback").textContent = error.message; }
});
poll();
