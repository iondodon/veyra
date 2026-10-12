"use strict";
const list = document.getElementById("timeline");
const statusLine = document.getElementById("timeline-status");
const button = document.getElementById("load-more");
let offset = 0;
let loading = false;
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function versionCard(version) {
  const item = element("li", null, "version-card");
  if (version.current) item.append(element("span", "Running version", "badge"));
  item.append(element("h2", version.title));
  const meta = element("p", null, "meta");
  const date = new Date(version.date);
  const time = element("time", Number.isNaN(date.getTime()) ? version.date : date.toLocaleString());
  time.dateTime = version.date;
  meta.append(time, document.createTextNode(" · "), element("code", version.commit.slice(0, 12)));
  item.append(meta);
  const details = element("details");
  details.append(element("summary", "Commit ID & release notes"), element("p", version.commit, "commit-id"));
  if (version.notes) {
    details.append(element("h3", version.notes.summary), element("p", version.notes.instructions, "instructions"));
    const links = element("div", null, "release-links");
    for (const link of version.notes.links) {
      // Defense in depth: never turn historical data into an external or script URL.
      const url = new URL(link.path, location.origin);
      if (url.origin !== location.origin || !link.path.startsWith("/") || link.path.startsWith("//")) continue;
      const anchor = element("a", link.label);
      anchor.setAttribute("href", link.path);
      links.append(anchor);
    }
    details.append(links, element("p", "Notes saved in this version; they may repeat from an earlier release. Historical links may no longer be available.", "note"));
  } else {
    details.append(element("p", "No release notes available for this version.", "note"));
  }
  item.append(details);
  return item;
}
async function load() {
  if (loading || offset == null) return;
  loading = true;
  button.disabled = true;
  statusLine.textContent = "Loading version history…";
  statusLine.classList.remove("error");
  try {
    const response = await fetch(`/api/versions?offset=${offset}`, {cache: "no-store", signal: AbortSignal.timeout(30000)});
    if (!response.ok) throw new Error(response.status === 503 ? "Version history is unavailable. Try again or open Recovery." : "Cannot load history. Check your connection or sign-in.");
    const data = await response.json();
    const cards = data.versions.map(versionCard);
    list.append(...cards);
    offset = data.next_offset;
    button.hidden = offset == null;
    button.textContent = "Load older versions";
    statusLine.textContent = list.children.length ? `${list.children.length} versions shown${offset == null ? " · End of history" : ""}.` : "No committed versions found.";
  } catch (error) {
    statusLine.textContent = error.message;
    statusLine.classList.add("error");
    button.hidden = false;
    button.textContent = "Try again";
  } finally {
    loading = false;
    button.disabled = false;
  }
}
button.addEventListener("click", load);
load();
