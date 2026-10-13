"use strict";
// Vendored Marked + DOMPurify: no CDN, network images, or executable model HTML.
window.VeyraMarkdown = {
  render(element, text) {
    if (!window.marked || !window.DOMPurify?.isSupported) {
      element.textContent = text;
      return;
    }
    const fragment = window.DOMPurify.sanitize(window.marked.parse(text, {gfm: true}), {
      ALLOWED_TAGS: ["p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "del",
        "blockquote", "ul", "ol", "li", "pre", "code", "a", "table", "thead", "tbody", "tr", "th", "td"],
      ALLOWED_ATTR: ["href", "title", "start"],
      ALLOW_DATA_ATTR: false,
      ALLOW_ARIA_ATTR: false,
      RETURN_DOM_FRAGMENT: true,
    });
    for (const link of fragment.querySelectorAll("a")) {
      const href = link.getAttribute("href");
      try {
        const url = new URL(href, window.location.href);
        if (!href || !["http:", "https:"].includes(url.protocol)) throw new Error("Unsafe link");
        link.href = url.href;
        if (url.origin !== window.location.origin) {
          link.target = "_blank";
          link.rel = "noopener noreferrer";
        }
      } catch (_) { link.removeAttribute("href"); }
    }
    element.classList.add("markdown");
    element.replaceChildren(fragment);
  },
};
