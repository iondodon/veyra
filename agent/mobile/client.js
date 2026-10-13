// Native requests use the existing authenticated web API; no server bypass.
const { Buffer } = require('buffer');
function normalizeServer(value) {
  const url = new URL(value.trim());
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname))
    throw new Error('Enter only the server address, such as http://192.168.1.20:3000.');
  return url.origin;
}
function createClient(server, password, transport = fetch) {
  const origin = normalizeServer(server);
  return async (path, body, signal) => {
    const response = await transport(origin + path, {
      method: body === undefined ? 'GET' : 'POST', signal,
      headers: {Authorization: 'Basic ' + Buffer.from('veyra:' + password, 'utf8').toString('base64'),
        ...(body === undefined ? {} : {'Content-Type': 'application/json', Origin: origin})},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(response.status === 401 ? 'Incorrect Veyra network password.' : data.error || 'Request failed.');
    return data;
  };
}
function mergeSnapshot(previous, snapshot) {
  const restarted = previous.status.session_id && previous.status.session_id !== snapshot.status.session_id;
  const events = restarted ? [] : previous.events;
  const ids = new Set(events.map(event => event.id));
  return {events: [...events, ...snapshot.events.filter(event => !ids.has(event.id))],
    cursor: snapshot.cursor, status: snapshot.status};
}
module.exports = {normalizeServer, createClient, mergeSnapshot};
