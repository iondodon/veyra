"use strict";
(() => {
  const version = document.querySelector('meta[name="veyra-ui-version"]')?.content;
  let pending = false;
  let reloading = false;
  let options = {poll: true, headers: () => ({}), canReload: () => true};
  const edits = new Map();

  function value(field) {
    if (field.matches('[type="checkbox"], [type="radio"]')) return field.checked;
    return field.isContentEditable ? field.innerHTML : field.value;
  }
  function editable(target) {
    return target instanceof Element && target.closest('input, textarea, select, [contenteditable="true"]');
  }
  document.addEventListener("focusin", event => {
    const field = editable(event.target);
    if (field && !edits.has(field)) edits.set(field, value(field));
  });
  for (const type of ["input", "change"]) document.addEventListener(type, event => {
    const field = editable(event.target);
    if (field && !edits.has(field)) edits.set(field, field.defaultValue ?? "");
  });
  function saved(scope = document) {
    for (const field of edits.keys()) if (scope.contains(field)) edits.delete(field);
  }
  document.addEventListener("reset", event => saved(event.target));

  function editing() {
    if (document.querySelector("dialog[open]")) return true;
    for (const [field, initial] of edits) {
      if (!field.isConnected) edits.delete(field);
      else if (value(field) !== initial) return true;
    }
    return false;
  }

  function check(status) {
    if (!status.ready) return reloading;
    pending = Boolean(version && status.ui_version && version !== status.ui_version);
    if (pending && !reloading && !editing() && options.canReload()) {
      reloading = true;
      window.location.reload();
    }
    window.dispatchEvent(new Event("veyra:update"));
    // Pages without the chat's activity indicator still explain a deferred update.
    if (!document.querySelector("#activity")) {
      let notice = document.querySelector("#veyra-update-notice");
      if (pending && !notice) {
        notice = document.createElement("p");
        notice.id = "veyra-update-notice";
        notice.setAttribute("role", "status");
        notice.textContent = "An update is ready. Finish your edits or close settings to load it automatically.";
        document.body.prepend(notice);
      }
      if (notice) notice.hidden = !pending;
    }
    return reloading;
  }

  window.VeyraUpdates = {
    configure: settings => { options = {...options, ...settings}; },
    saved,
    check,
    get pending() { return pending; },
  };

  async function poll() {
    if (options.poll) {
      try {
        const response = await fetch("/api/app-version", {cache: "no-store", headers: options.headers()});
        if (response.ok) check(await response.json());
      } catch (_) { /* Wait through restarts and temporary network failures. */ }
    }
    if (!reloading) window.setTimeout(poll, 1000);
  }
  window.setTimeout(poll, 1000);
})();
