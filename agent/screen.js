"use strict";
(() => {
  const button = document.querySelector('#screen-toggle');
  const status = document.querySelector('#screen-status');
  const image = document.querySelector('#host-screen');
  let active = false, timer = null, controller = null, imageURL = null;
  function clearImage() {
    image.hidden = true;
    image.removeAttribute('src');
    if (imageURL) URL.revokeObjectURL(imageURL);
    imageURL = null;
  }
  function pause() {
    clearTimeout(timer);
    controller?.abort();
    clearImage();
  }
  async function refresh() {
    if (!active || document.hidden) return;
    const request = new AbortController();
    controller = request;
    try {
      const response = await fetch('/api/screen', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}', cache: 'no-store', signal: request.signal});
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Screen capture unavailable.');
      }
      const blob = await response.blob();
      if (request.signal.aborted || !active || document.hidden) return;
      clearImage();
      imageURL = URL.createObjectURL(blob);
      image.src = imageURL;
      image.hidden = false;
      status.className = '';
      status.textContent = 'Viewing · updated ' + new Date().toLocaleTimeString();
    } catch (error) {
      if (request.signal.aborted) return;
      clearImage();
      status.className = 'error';
      status.textContent = error.message;
    } finally {
      if (controller === request) {
        controller = null;
        if (active && !document.hidden) timer = setTimeout(refresh, 2000);
      }
    }
  }
  button.addEventListener('click', () => {
    active = !active;
    button.textContent = active ? 'Stop viewing' : 'Start viewing';
    if (active) { status.textContent = 'Capturing desktop…'; refresh(); }
    else { pause(); status.className = ''; status.textContent = 'Not sharing.'; }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { pause(); if (active) status.textContent = 'Viewing paused while this tab is hidden.'; }
    else if (active) refresh();
  });
  window.addEventListener('pagehide', pause);
})();
