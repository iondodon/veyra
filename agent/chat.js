"use strict";
const messages = document.querySelector("#messages");
const conversation = document.querySelector("#conversation");
const viewportStyle = document.documentElement.style;
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
let attachments = [];
let readingImages = false;
let cursor = 0;
let state = {ready: false, busy: false, approval_id: null};
let posting = false;
let connected = false;
let notice = "";
let authorization = "";
let passwordSaving = false;
let keySaving = false;
let approvalSaving = false;
let canSaveKeys = false;
let pollComplete = Promise.resolve();
let followingLatest = true;
let touchingConversation = false;
let touchEndTimer;
let viewportFrame;
let restingViewportHeight = window.innerHeight;
let conversationHeight = conversation.clientHeight;

function scrollToLatest() {
  followingLatest = true;
  conversation.scrollTop = conversation.scrollHeight;
}

conversation.addEventListener("scroll", () => {
  // A keyboard or composer resize changes the scroll range, not the reader's intent.
  if (conversation.clientHeight !== conversationHeight) return;
  followingLatest = conversation.scrollHeight - conversation.clientHeight - conversation.scrollTop <= 32;
}, {passive: true});
conversation.addEventListener("touchstart", () => {
  window.clearTimeout(touchEndTimer);
  touchingConversation = true;
}, {passive: true});
for (const type of ["touchend", "touchcancel"]) conversation.addEventListener(type, () => {
  touchEndTimer = window.setTimeout(() => { touchingConversation = false; }, 250);
}, {passive: true});

function syncViewport() {
  viewportFrame = null;
  const viewport = window.visualViewport;
  // Let native pinch zoom magnify the page without reflowing the layout.
  if (viewport && Math.abs(viewport.scale - 1) > .01) return;
  const height = viewport ? viewport.height : window.innerHeight;
  const editing = document.activeElement?.matches("input, textarea, select, [contenteditable='true']");
  if (!editing && !document.body.classList.contains("keyboard-open")) restingViewportHeight = height;
  const keyboardOpen = editing && restingViewportHeight - height > 120;
  document.body.classList.toggle("keyboard-open", Boolean(keyboardOpen));
  viewportStyle.setProperty("--chat-height", height + "px");
  viewportStyle.setProperty("--chat-top", (viewport ? viewport.offsetTop : 0) + "px");
}

function scheduleViewport() {
  if (viewportFrame == null) viewportFrame = window.requestAnimationFrame(syncViewport);
}
window.addEventListener("resize", scheduleViewport);
window.visualViewport?.addEventListener("resize", scheduleViewport);
window.visualViewport?.addEventListener("scroll", scheduleViewport);
document.addEventListener("focusin", scheduleViewport);
document.addEventListener("focusout", scheduleViewport);
const composerFooter = document.querySelector("footer");
const layoutObserver = new ResizeObserver(() => {
  viewportStyle.setProperty("--composer-height", composerFooter.getBoundingClientRect().height + "px");
  conversationHeight = conversation.clientHeight;
  if (followingLatest && !touchingConversation) scrollToLatest();
});
layoutObserver.observe(conversation);
layoutObserver.observe(composerFooter);
syncViewport();

window.VeyraUpdates.configure({poll: false,
  canReload: () => !input.value && !attachments.length && !readingImages && !posting && !passwordSaving && !keySaving && !approvalSaving});
window.addEventListener("veyra:update", refreshControls);

function requestHeaders(json = false) {
  return {...(json ? {"Content-Type": "application/json"} : {}),
    ...(authorization ? {Authorization: authorization} : {})};
}

async function setAutoApprove(enabled) {
  if (enabled && !window.confirm("Allow ALL new shell command requests to run without review? Commands can delete files, install software, or change this computer. This lasts until restart, service disconnect/change, or you turn it off. Existing pending approvals still require a decision.")) return;
  approvalSaving = true;
  refreshControls();
  try {
    const response = await fetch("/api/auto-approve", {method: "POST", headers: requestHeaders(true),
      body: JSON.stringify({enabled, session_id: state.session_id})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not change auto-approval.");
    state.auto_approve = result.auto_approve;
    notice = "";
  } catch (error) { notice = error.message; }
  finally { approvalSaving = false; refreshControls(); }
}
document.querySelector("#auto-approve-toggle").addEventListener("click", () => setAutoApprove(!state.auto_approve));
document.querySelector("#auto-approve-off").addEventListener("click", () => setAutoApprove(false));

function refreshControls() {
  const toggle = document.querySelector("#auto-approve-toggle");
  toggle.textContent = "Auto-approve commands: " + (state.auto_approve ? "On" : "Off");
  toggle.setAttribute("aria-pressed", String(Boolean(state.auto_approve)));
  toggle.disabled = !connected || !state.ready || approvalSaving || (!state.auto_approve && (!state.configuration?.connected || Boolean(state.approval_id)));
  document.querySelector("#auto-approve-warning").hidden = !state.auto_approve;
  document.querySelector("#auto-approve-off").disabled = !connected || !state.ready || approvalSaving;
  const available = connected && state.ready && !state.busy && !posting && !passwordSaving && !keySaving;
  document.querySelectorAll("#image-previews button").forEach(button => { button.disabled = posting; });
  sendButton.disabled = !available || readingImages || (!input.value.trim() && !attachments.length);
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
  // Plain-text messages and command output: only http(s) URLs become links.
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
  if (event.role === "assistant" && event.kind !== "approval") {
    window.VeyraMarkdown.render(body, event.text);
  } else {
    appendText(body, event.text);
  }
  article.append(label, body);
  for (const image of event.images || []) {
    if (!/^data:image\/(png|jpeg|webp|gif);base64,/.test(image.data_url)) continue;
    const img = document.createElement("img");
    img.src = image.data_url; img.alt = image.name; img.className = "chat-image";
    article.append(img);
  }
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
      if (data.events.length) {
        const follow = followingLatest && !touchingConversation;
        data.events.forEach(appendMessage);
        if (follow) scrollToLatest();
      }
      cursor = data.cursor;
      state = data.status;
      more = data.has_more;
    } while (more);
    connected = true;
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

async function submit(text, approvalId = null, images = []) {
  if (posting || !connected || !state.ready || state.busy || passwordSaving || keySaving) return;
  posting = true;
  notice = "";
  refreshControls();
  try {
    const response = await fetch("/api/messages", {
      method: "POST", headers: requestHeaders(true),
      body: JSON.stringify({text, approval_id: approvalId, images}),
    });
    const result = await response.json();
    if (result.open_api_keys) {
      input.value = "";
      input.style.height = "";
      openApiKeys();
    }
    if (!response.ok) throw new Error(result.error || "Could not send your message.");
    state.busy = true;
    if (images.length) { attachments = []; renderAttachments(); }
    window.VeyraUpdates.saved(document.querySelector("#composer"));
    if (input.value.trim() === text) { input.value = ""; input.style.height = ""; }
    document.querySelectorAll("details").forEach(details => { details.open = false; });
    scrollToLatest();
  } catch (error) { notice = error.message; }
  finally { posting = false; refreshControls(); input.focus({preventScroll: true}); }
}

document.querySelector("#composer").addEventListener("submit", event => {
  event.preventDefault();
  const text = input.value.trim();
  submit(text, /^(?:\/approve|\/deny|approve(?: these commands)?|deny(?: these commands)?)[.!]?$/i.test(text) ? state.approval_id : null, attachments.slice());
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

function renderAttachments() {
  const previews = document.querySelector("#image-previews");
  previews.replaceChildren();
  attachments.forEach((image, index) => {
    const item = document.createElement("div");
    const img = document.createElement("img"); img.src = image.data_url; img.alt = image.name;
    const remove = document.createElement("button"); remove.type = "button";
    remove.textContent = "Remove " + image.name;
    remove.disabled = posting;
    remove.addEventListener("click", () => { attachments.splice(index, 1); renderAttachments(); });
    item.append(img, remove); previews.append(item);
  });
  refreshControls();
}
document.querySelector("#attach").addEventListener("click", () => {
  if (!posting && !readingImages) document.querySelector("#image-files").click();
});
document.querySelector("#image-files").addEventListener("change", async event => {
  const files = [...event.target.files]; event.target.value = "";
  if (readingImages || posting) return;
  readingImages = true; refreshControls();
  try {
    if (attachments.length + files.length > 4) throw new Error("Attach up to 4 images.");
    const added = [];
    for (const file of files) {
      if (!["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type) || file.size > 5 * 1024 * 1024)
        throw new Error("Use PNG, JPEG, WebP or GIF images, at most 5 MB each.");
      const data_url = await new Promise((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("Could not read image.")); reader.readAsDataURL(file);
      });
      added.push({name: file.name.slice(0, 255), data_url});
    }
    attachments.push(...added); notice = "";
  } catch (error) { notice = error.message; }
  finally { readingImages = false; renderAttachments(); }
});
