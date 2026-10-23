'use strict';
window.addEventListener('DOMContentLoaded', () => {
let running = false;
const status = document.querySelector('#status');
const button = document.querySelector('#inspect');
window.VeyraUpdates?.configure({canReload: () => !running});
async function refresh() {
  try {
    const response = await fetch('/api/admin');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    running = data.running;
    button.disabled = running;
    status.textContent = data.message;
    document.querySelector('#output').textContent = data.output;
  } catch (error) { status.textContent = error.message; button.disabled = true; }
}
button.addEventListener('click', async () => {
  running = true; button.disabled = true;
  try {
    const response = await fetch('/api/admin', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'firewall'})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    await refresh();
  } catch (error) { running = false; button.disabled = false; status.textContent = error.message; }
});
refresh();
setInterval(refresh, 1500);

});
