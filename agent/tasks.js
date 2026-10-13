"use strict";
const list = document.querySelector('#tasks');
const statusLine = document.querySelector('#task-status');
let previous = '';
const labels = {working: 'Working', approval: 'Awaiting approval', finished: 'Finished', interrupted: 'Interrupted'};
function render(tasks) {
  list.replaceChildren();
  if (!tasks.length) { list.textContent = 'No requests in this runtime session yet. Send a message in chat to begin.'; return; }
  for (const task of tasks) {
    const card = document.createElement('section'); card.className = 'card';
    const heading = document.createElement('div'); heading.className = 'task-heading';
    const title = document.createElement('h2'); title.textContent = 'Request';
    const badge = document.createElement('span'); badge.className = 'badge ' + task.state; badge.textContent = labels[task.state] || task.state;
    heading.append(title, badge);
    const request = document.createElement('p'); request.className = 'request'; request.textContent = task.request;
    const time = document.createElement('p'); time.className = 'note'; time.textContent = 'Started ' + new Date(task.started).toLocaleString() + (task.finished ? ' · Ended ' + new Date(task.finished).toLocaleString() : '');
    const events = document.createElement('ol'); events.className = 'task-events';
    for (const event of task.events) {
      const item = document.createElement('li');
      const label = document.createElement('span'); label.className = 'event-label'; label.textContent = event.kind + ' · ' + new Date(event.at).toLocaleTimeString();
      const text = document.createElement('pre'); text.textContent = event.text;
      item.append(label, text); events.append(item);
    }
    card.append(heading, request, time, events); list.append(card);
  }
}
async function poll() {
  if (!document.hidden) {
    try {
      const response = await fetch('/api/tasks', {cache: 'no-store'});
      if (!response.ok) throw new Error('Connection unavailable');
      const data = await response.json();
      if (window.VeyraUpdates.check(data)) return;
      const serialized = JSON.stringify(data.tasks);
      if (serialized !== previous) { render(data.tasks); previous = serialized; }
      statusLine.textContent = data.ready ? 'Live · updates every second' : 'Runtime is starting or disconnected';
    } catch (_) { statusLine.textContent = 'Disconnected — retrying. Displayed progress may be stale.'; }
  }
  window.setTimeout(poll, 1000);
}
poll();
