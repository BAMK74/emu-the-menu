// Settings and Eaters open as one overlay on every page, as two tabs. Each panel is fetched when it
// is opened, so pages do not carry that data. Without scripting, or if a fetch fails, the links
// simply go to the Settings and Eaters pages. Changes to eaters are sent from inside the overlay;
// closing it reloads the page so everything behind it shows the new eaters.
(function () {
  const dialog = document.getElementById("settings-dialog");
  const link = document.getElementById("settings-link");
  const panels = { settings: document.getElementById("settings-panel"), eaters: document.getElementById("eaters-panel") };
  if (!dialog || !link || typeof dialog.showModal !== "function") return;
  let dirty = false;

  function show(name) {
    for (const [key, el] of Object.entries(panels)) el.hidden = key !== name;
    dialog.querySelectorAll(".dlg-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  }
  function renderEaters(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const content = doc.querySelector("main .split") || doc.querySelector("main");
    panels.eaters.innerHTML = content ? content.innerHTML : "<p>Eaters could not be loaded.</p>";
    // The page's own script (the calorie hints) does not run when inserted as text, so run it here.
    doc.querySelectorAll("main script").forEach((old) => { try { new Function(old.textContent)(); } catch (e) { /* hints only */ } });
  }
  async function open(name) {
    try {
      if (name === "eaters") {
        const response = await fetch("/eaters");
        if (!response.ok) throw new Error("HTTP " + response.status);
        renderEaters(await response.text());
      } else {
        const response = await fetch("/settings/panel?next=" + encodeURIComponent(window.location.pathname));
        if (!response.ok) throw new Error("HTTP " + response.status);
        panels.settings.innerHTML = await response.text();
      }
      show(name);
      if (!dialog.open) dialog.showModal();
    } catch (error) {
      window.location.href = name === "eaters" ? "/eaters" : link.href;
    }
  }

  link.addEventListener("click", (event) => { event.preventDefault(); open("settings"); });
  // Any link to Eaters, on any page but the Eaters page itself, opens the overlay instead.
  document.addEventListener("click", (event) => {
    const a = event.target.closest('a[href="/eaters"]');
    if (!a || event.defaultPrevented || event.metaKey || event.ctrlKey || location.pathname === "/eaters") return;
    event.preventDefault(); open("eaters");
  });
  dialog.querySelectorAll(".dlg-tabs button").forEach((b) => b.addEventListener("click", () => open(b.dataset.tab)));
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-close-dialog]")) dialog.close();
  });
  dialog.addEventListener("close", () => { if (dirty) window.location.reload(); });

  // Eaters forms are sent from inside the overlay, and the answer replaces the list.
  panels.eaters.addEventListener("submit", async (event) => {
    const form = event.target.closest("form");
    if (!form) return;
    event.preventDefault();
    event.stopPropagation();
    if (form.dataset.confirm && !confirm(form.dataset.confirm)) return;
    try {
      const response = await fetch(form.action, { method: (form.method || "post").toUpperCase(), body: new URLSearchParams(new FormData(form)) });
      renderEaters(await response.text());
      if (response.ok) dirty = true;
    } catch (error) {
      form.submit();
    }
  });
})();
