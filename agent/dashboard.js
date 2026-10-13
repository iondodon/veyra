"use strict";
const statusLine = document.querySelector("#dashboard-status");
const refreshButton = document.querySelector("#refresh");
let loading = false;
let timer;
const labels = {chatgpt: "ChatGPT subscription", claude: "Claude subscription", openai: "OpenAI API", anthropic: "Anthropic API"};
function text(id, value) { document.getElementById(id).textContent = value ?? "Unavailable"; }
function duration(seconds) {
  if (seconds == null) return "Unavailable";
  const minutes = Math.floor(seconds / 60);
  return `${Math.floor(minutes / 1440)}d ${Math.floor(minutes / 60) % 24}h ${minutes % 60}m`;
}
function bytes(value) {
  if (value == null) return "Unavailable";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return value.toFixed(i ? 1 : 0) + " " + units[i];
}
function resource(id, data) {
  const bar = document.getElementById(id + "-bar");
  bar.hidden = !data || !data.total_bytes;
  if (bar.hidden) { text(id, "Unavailable"); text(id + "-detail", "Metric not available on this host."); return; }
  const percent = 100 * data.used_bytes / data.total_bytes;
  text(id, percent.toFixed(1) + "% used");
  bar.value = percent;
  text(id + "-detail", bytes(data.used_bytes) + " of " + bytes(data.total_bytes) + " · " + bytes(data.free_bytes ?? data.total_bytes - data.used_bytes) + " available");
}
async function refresh() {
  if (loading) return;
  clearTimeout(timer);
  loading = true;
  refreshButton.disabled = true;
  try {
    const response = await fetch("/api/dashboard", {cache: "no-store", signal: AbortSignal.timeout(10000)});
    if (response.status === 503) throw new Error("Veyra is unavailable. For recovery, open http://127.0.0.1:3001 on the computer running Veyra.");
    if (!response.ok) throw new Error("Cannot load system status. Check your connection or sign-in.");
    const data = await response.json();
    const agent = data.agent;
    text("runtime", !agent.ready ? "Starting" : agent.busy ? "Working" : agent.approval_pending ? "Awaiting approval" : "Ready");
    text("agent-uptime", duration(agent.uptime_seconds));
    text("approval", agent.approval_pending ? "Awaiting your review in Chat" : "None pending");
    text("provider", labels[agent.provider] || "Not selected");
    text("model", agent.model || "Not selected");
    text("service", agent.service_connected ? "Connected" : "Not connected");
    text("os", data.os || "Unavailable");
    text("host-uptime", duration(data.host_uptime_seconds));
    text("cpus", data.cpu_count);
    text("load", data.load_average?.map(value => value.toFixed(2)).join(" / "));
    resource("memory", data.memory);
    resource("disk", data.disk);
    text("branch", data.version.branch || (data.version.commit ? "Detached HEAD" : "Unavailable"));
    text("commit", data.version.commit);
    text("changes", data.version.dirty == null ? "Unavailable" : data.version.dirty ? "Uncommitted changes" : "Clean");
    statusLine.textContent = "Updated " + new Date(data.sampled_at * 1000).toLocaleTimeString();
    statusLine.classList.remove("error");
  } catch (error) {
    statusLine.textContent = error.message + " Displayed values may be stale.";
    statusLine.classList.add("error");
  } finally {
    loading = false;
    refreshButton.disabled = false;
    if (!document.hidden) timer = setTimeout(refresh, 5000);
  }
}
refreshButton.addEventListener("click", refresh);
document.addEventListener("visibilitychange", () => {
  clearTimeout(timer);
  if (!document.hidden) refresh();
});
refresh();
