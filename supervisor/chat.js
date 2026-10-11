"use strict";
const messages = document.querySelector("#messages");
const input = document.querySelector("#message");
const sendButton = document.querySelector("#send");
const activity = document.querySelector("#activity");
const connection = document.querySelector("#connection");
const passwordDialog = document.querySelector("#password-dialog");
const passwordForm = document.querySelector("#password-form");
const passwordStatus = document.querySelector("#password-status");
let cursor = 0;
let state = {ready: false, busy: false, approval_id: null};
let posting = false;
let connected = false;
let notice = "";
let authorization = "";
let passwordSaving = false;
let pollComplete = Promise.resolve();

function requestHeaders(json = false) {
  return {...(json ? {"Content-Type": "application/json"} : {}),
    ...(authorization ? {Authorization: authorization} : {})};
}

function refreshControls() {
  const available = connected && state.ready && !state.busy && !posting && !passwordSaving;
  sendButton.disabled = !available || !input.value.trim();
  document.querySelectorAll("[data-command]").forEach(button => { button.disabled = !available; });
  document.querySelectorAll("[data-approval]").forEach(button => {
    button.disabled = !available || button.dataset.approval !== state.approval_id;
  });
  activity.textContent = notice || (!connected ? "Reconnecting to Veyra…" : !state.ready ?
    "Veyra is starting…" : state.busy ? "Veyra is working…" : state.approval_id ?
    "Review the requested commands and approve or deny them." : "");
  connection.textContent = connected && state.ready ? "Connected" : connected ? "Starting" : "Reconnecting";
  connection.classList.toggle("online", connected && state.ready);
  document.querySelector("#password-reminder").hidden = !state.password_is_default;
  document.body.classList.toggle("password-required", Boolean(state.password_is_default));
}

function appendText(element, text) {
  // Treat model and command output as text. Only http(s) URLs become links.
  const pattern = /https?:\/\/[^\s<>"']+/g;
  let end = 0;
  for (const match of text.matchAll(pattern)) {
    element.append(document.createTextNode(text.slice(end, match.index)));
    const link = document.createElement("a");
    link.href = match[0];
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = match[0].startsWith("https://auth.openai.com/") ? "Continue with ChatGPT ↗" : match[0];
    element.append(link);
    end = match.index + match[0].length;
  }
  element.append(document.createTextNode(text.slice(end)));
}

function appendMessage(event) {
  const article = document.createElement("article");
  article.classList.add("message");
  article.classList.add(["user", "assistant", "system", "tool"].includes(event.role) ? event.role : "system");
  if (event.kind === "approval") article.classList.add("approval");
  const label = document.createElement("div");
  label.className = "label";
  label.textContent = {user: "You", assistant: "Veyra", system: "Veyra", tool: "Command output"}[event.role] || "Veyra";
  const body = document.createElement("div");
  body.className = "body";
  appendText(body, event.text);
  article.append(label, body);
  if (event.kind === "approval") {
    const actions = document.createElement("div");
    actions.className = "approval-actions";
    for (const [command, title] of [["/approve", "Approve commands"], ["/deny", "Deny"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = title;
      button.dataset.approval = event.approval_id;
      if (command === "/approve") button.className = "approve";
      button.addEventListener("click", () => submit(command, event.approval_id));
      actions.append(button);
    }
    article.append(actions);
  }
  messages.append(article);
}

async function loadEvents() {
  try {
    let more;
    const follow = window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
    do {
      const response = await fetch("/api/events?after=" + cursor, {cache: "no-store", headers: requestHeaders()});
      if (!response.ok) throw new Error("Connection unavailable");
      const data = await response.json();
      data.events.forEach(appendMessage);
      cursor = data.cursor;
      state = data.status;
      more = data.has_more;
    } while (more);
    connected = true;
    if (follow) window.scrollTo(0, document.body.scrollHeight);
  } catch (_) { connected = false; }
  refreshControls();
}

async function poll() {
  if (!passwordSaving) {
    pollComplete = loadEvents();
    await pollComplete;
  }
  window.setTimeout(poll, 500);
}

async function submit(text, approvalId = null) {
  if (posting || !connected || !state.ready || state.busy || passwordSaving) return;
  posting = true;
  notice = "";
  refreshControls();
  try {
    const response = await fetch("/api/messages", {
      method: "POST", headers: requestHeaders(true),
      body: JSON.stringify({text, approval_id: approvalId}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not send your message.");
    state.busy = true;
    if (input.value.trim() === text) { input.value = ""; input.style.height = ""; }
    document.querySelectorAll("details").forEach(details => { details.open = false; });
    window.scrollTo(0, document.body.scrollHeight);
  } catch (error) { notice = error.message; }
  finally { posting = false; refreshControls(); input.focus(); }
}

document.querySelector("#composer").addEventListener("submit", event => {
  event.preventDefault();
  const text = input.value.trim();
  submit(text, ["/approve", "/deny"].includes(text) ? state.approval_id : null);
});
input.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!sendButton.disabled) document.querySelector("#composer").requestSubmit();
  }
});
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 180) + "px";
  refreshControls();
});
document.querySelectorAll("[data-command]").forEach(button => {
  button.addEventListener("click", () => submit(button.dataset.command));
});
document.querySelectorAll("[data-password-settings]").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll("details").forEach(details => { details.open = false; });
    passwordDialog.showModal();
  });
});
document.querySelector("#password-cancel").addEventListener("click", () => passwordDialog.close());
passwordDialog.addEventListener("cancel", event => { if (passwordSaving) event.preventDefault(); });
passwordDialog.addEventListener("close", () => {
  passwordForm.reset();
  passwordStatus.textContent = "";
});
passwordForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (passwordSaving) return;
  const current = document.querySelector("#current-password").value;
  const password = document.querySelector("#new-password").value;
  if (password !== document.querySelector("#confirm-password").value) {
    passwordStatus.textContent = "The new passwords do not match.";
    return;
  }
  passwordSaving = true;
  passwordStatus.textContent = "Saving…";
  document.querySelector("#password-save").disabled = true;
  document.querySelector("#password-cancel").disabled = true;
  refreshControls();
  try {
    // Finish the current poll before changing the credentials it uses.
    await pollComplete;
    const response = await fetch("/api/password", {
      method: "POST", headers: requestHeaders(true),
      body: JSON.stringify({current_password: current, new_password: password}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not update the password.");
    // Keep this tab connected; browsers ask for the new password on the next sign-in.
    // Credentials stay in memory and are never sent to the chat or saved in browser storage.
    authorization = "Basic " + btoa(String.fromCharCode(...new TextEncoder().encode("veyra:" + password)));
    state.password_is_default = false;
    notice = "Password updated. Use your new password the next time you sign in.";
    passwordDialog.close();
  } catch (error) { passwordStatus.textContent = error.message; }
  finally {
    passwordSaving = false;
    document.querySelector("#password-save").disabled = false;
    document.querySelector("#password-cancel").disabled = false;
    refreshControls();
  }
});
poll();
