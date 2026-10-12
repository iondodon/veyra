"use strict";
const messages = document.querySelector("#messages");
const input = document.querySelector("#message");
const sendButton = document.querySelector("#send");
const activity = document.querySelector("#activity");
const connection = document.querySelector("#connection");
const passwordDialog = document.querySelector("#password-dialog");
const passwordForm = document.querySelector("#password-form");
const passwordStatus = document.querySelector("#password-status");
const apiDialog = document.querySelector("#api-dialog");
const apiForm = document.querySelector("#api-form");
const apiStatus = document.querySelector("#api-status");
const apiKey = document.querySelector("#api-key");
let cursor = 0;
let state = {ready: false, busy: false, approval_id: null};
let posting = false;
let connected = false;
let notice = "";
let authorization = "";
let passwordSaving = false;
let keySaving = false;
let canSaveKeys = false;
let pollComplete = Promise.resolve();
window.VeyraUpdates.configure({poll: false,
  canReload: () => !input.value && !posting && !passwordSaving && !keySaving});
window.addEventListener("veyra:update", refreshControls);

function requestHeaders(json = false) {
  return {...(json ? {"Content-Type": "application/json"} : {}),
    ...(authorization ? {Authorization: authorization} : {})};
}

function refreshControls() {
  const available = connected && state.ready && !state.busy && !posting && !passwordSaving && !keySaving;
  sendButton.disabled = !available || !input.value.trim();
  document.querySelectorAll("[data-message]").forEach(button => { button.disabled = !available; });
  document.querySelectorAll("[data-approval]").forEach(button => {
    button.disabled = !available || button.dataset.approval !== state.approval_id;
  });
  activity.textContent = (window.VeyraUpdates.pending ? "An update is ready. Finish your draft or close settings to load it automatically." : notice) || (!connected ? "Reconnecting to Veyra…" : !state.ready ?
    "Veyra is starting…" : state.busy ? "Veyra is working…" : state.approval_id ?
    "Review the requested commands and approve or deny them." : "");
  connection.textContent = connected && state.ready ? "Connected" : connected ? "Starting" : "Reconnecting";
  connection.classList.toggle("online", connected && state.ready);
  document.querySelector("#password-reminder").hidden = !state.password_is_default;
  document.body.classList.toggle("password-required", Boolean(state.password_is_default));
  const configuration = state.configuration || {};
  const label = {chatgpt: "ChatGPT subscription", claude: "Claude subscription", openai: "OpenAI API", anthropic: "Anthropic API"}[configuration.provider];
  document.querySelector("#model-status").textContent = label ?
    label + (configuration.model ? " · " + configuration.model : " · awaiting setup") : "Choose a service to begin";
  const needsSignIn = ["chatgpt", "claude"].includes(configuration.provider) && !configuration.connected;
  const signInLabel = configuration.provider === "claude" ? "Continue with Claude" : "Continue with ChatGPT";
  document.querySelectorAll("[data-signin-link]").forEach(link => {
    link.hidden = !state.local_client || !needsSignIn || !configuration.sign_in_url;
    link.textContent = signInLabel + " ↗";
    if (configuration.sign_in_url) link.href = configuration.sign_in_url;
    else link.removeAttribute("href");
  });
  document.querySelectorAll("[data-subscription-choice]").forEach(button => {
    const provider = button.dataset.subscriptionChoice;
    const name = provider === "claude" ? "Claude" : "ChatGPT";
    const pending = provider === configuration.provider && needsSignIn;
    button.hidden = state.local_client && pending && Boolean(configuration.sign_in_url);
    button.textContent = pending && !state.local_client ? "Open " + name + " sign-in on Veyra’s computer" : "Continue with " + name;
    button.dataset.message = pending ? "Sign in with " + name + "." : "Use my " + name + " subscription.";
  });
  document.querySelectorAll("[data-signin-help]").forEach(note => { note.hidden = !needsSignIn; });
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
  if (event.kind === "update" && Array.isArray(event.links)) {
    const actions = document.createElement("div");
    actions.className = "setup-actions";
    for (const item of event.links) {
      if (typeof item.path !== "string" || !item.path.startsWith("/")) continue;
      const url = new URL(item.path, window.location.origin);
      if (url.origin !== window.location.origin) continue;
      const link = document.createElement("a");
      link.href = url.href;
      link.textContent = item.label;
      actions.append(link);
    }
    article.append(actions);
  }
  if (event.kind === "approval") {
    const actions = document.createElement("div");
    actions.className = "approval-actions";
    for (const [command, title] of [["Approve these commands.", "Approve commands"], ["Deny these commands.", "Deny"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = title;
      button.dataset.approval = event.approval_id;
      if (command.startsWith("Approve")) button.className = "approve";
      button.addEventListener("click", () => submit(command, event.approval_id));
      actions.append(button);
    }
    article.append(actions);
  }
  if (event.kind === "setup" || event.kind === "key_request") {
    const actions = document.createElement("div");
    actions.className = "setup-actions";
    if (event.kind === "setup") {
      const link = document.createElement("a");
      link.dataset.signinLink = "";
      link.textContent = "Continue with ChatGPT ↗";
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.hidden = true;
      actions.append(link);
      for (const [provider, name] of [["chatgpt", "ChatGPT"], ["claude", "Claude"]]) {
        const signIn = document.createElement("button");
        signIn.type = "button";
        signIn.dataset.subscriptionChoice = provider;
        signIn.dataset.message = "Use my " + name + " subscription.";
        signIn.textContent = "Continue with " + name;
        signIn.addEventListener("click", () => submit(signIn.dataset.message));
        actions.append(signIn);
      }
    }
    const keys = document.createElement("button");
    keys.type = "button";
    keys.textContent = "Private API key form";
    keys.addEventListener("click", () => openApiKeys(event.provider));
    actions.append(keys);
    article.append(actions);
    if (event.kind === "setup") {
      const note = document.createElement("p");
      note.className = "setup-help";
      note.dataset.signinHelp = "";
      note.textContent = "Sign-in opens in a browser on the computer running Veyra. Finish signing in there.";
      article.append(note);
    }
  }
  messages.append(article);
}

async function loadEvents() {
  try {
    let more;
    const follow = window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
    do {
      const response = await fetch("/api/events?after=" + cursor, {cache: "no-store", headers: requestHeaders()});
      // The gateway stays online while the agent restarts. Keep polling here.
      if (response.status === 503) throw new Error("Veyra is restarting");
      if (!response.ok) throw new Error("Connection unavailable");
      const data = await response.json();
      if (window.VeyraUpdates.check(data.status)) return;
      if (state.session_id && state.session_id !== data.status.session_id) {
        messages.replaceChildren();
        cursor = 0;
        state = data.status;
        notice = "";
        more = true;
        continue;
      }
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
  if (posting || !connected || !state.ready || state.busy || passwordSaving || keySaving) return;
  posting = true;
  notice = "";
  refreshControls();
  try {
    const response = await fetch("/api/messages", {
      method: "POST", headers: requestHeaders(true),
      body: JSON.stringify({text, approval_id: approvalId}),
    });
    const result = await response.json();
    if (result.open_api_keys) {
      input.value = "";
      input.style.height = "";
      openApiKeys();
    }
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
  submit(text, /^(?:\/approve|\/deny|approve(?: these commands)?|deny(?: these commands)?)[.!]?$/i.test(text) ? state.approval_id : null);
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
document.querySelectorAll("[data-message]").forEach(button => {
  button.addEventListener("click", () => submit(button.dataset.message));
});
document.querySelectorAll("[data-api-keys]").forEach(button => {
  button.addEventListener("click", () => openApiKeys());
});

async function openApiKeys(provider) {
  document.querySelectorAll("details").forEach(details => { details.open = false; });
  apiForm.reset();
  if (["openai", "anthropic"].includes(provider)) document.querySelector("#api-provider").value = provider;
  canSaveKeys = false;
  apiKey.disabled = true;
  document.querySelector("#api-key-fields").hidden = true;
  document.querySelector("#api-local-instructions").hidden = true;
  document.querySelector("#api-summary").textContent = "";
  document.querySelector("#api-save").disabled = true;
  apiStatus.textContent = "Loading settings…";
  if (!apiDialog.open) apiDialog.showModal();
  try {
    const response = await fetch("/api/settings", {headers: requestHeaders(), cache: "no-store"});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load API settings.");
    canSaveKeys = result.can_save_keys;
    document.querySelector("#api-summary").textContent = result.providers.map(item =>
      item.key_name + ": " + (item.configured ? "configured" : "not set")).join(" · ");
    document.querySelector("#api-key-fields").hidden = !canSaveKeys;
    document.querySelector("#api-local-instructions").hidden = canSaveKeys;
    apiKey.disabled = !canSaveKeys;
    document.querySelector("#api-save").disabled = !canSaveKeys;
    apiStatus.textContent = "";
    if (canSaveKeys) apiKey.focus();
  } catch (error) { apiStatus.textContent = error.message; }
}

document.querySelector("#api-cancel").addEventListener("click", () => apiDialog.close());
apiDialog.addEventListener("cancel", event => { if (keySaving) event.preventDefault(); });
apiDialog.addEventListener("close", () => { apiForm.reset(); apiStatus.textContent = ""; });
apiForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (keySaving || !canSaveKeys) return;
  keySaving = true;
  apiStatus.textContent = "Saving…";
  document.querySelector("#api-save").disabled = true;
  document.querySelector("#api-cancel").disabled = true;
  refreshControls();
  try {
    const response = await fetch("/api/keys", {
      method: "POST", headers: requestHeaders(true),
      body: JSON.stringify({provider: document.querySelector("#api-provider").value, key: apiKey.value}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save the API key.");
    apiDialog.close();
  } catch (error) { apiStatus.textContent = error.message; }
  finally {
    apiKey.value = "";
    keySaving = false;
    document.querySelector("#api-save").disabled = !canSaveKeys;
    document.querySelector("#api-cancel").disabled = false;
    refreshControls();
  }
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
