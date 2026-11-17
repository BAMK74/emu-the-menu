// Click an ingredient to edit it in a popup. The form is fetched when it opens, so it always
// shows the ingredient as it is now. Without scripting the link opens the same form as a page.
(function () {
  const dialog = document.getElementById("ingredient-dialog");
  const panel = document.getElementById("ingredient-panel");
  document.addEventListener("click", async (event) => {
    const card = event.target.closest(".results li");
    const link = card && card.querySelector("a.card-link");
    if (!link || typeof dialog.showModal !== "function") return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    try {
      const response = await fetch(link.dataset.panel);
      if (!response.ok) throw new Error("HTTP " + response.status);
      panel.innerHTML = await response.text();
      dialog.showModal();
    } catch (error) {
      window.location.href = link.href;
    }
  });
  dialog.addEventListener("click", (event) => {
    const close = event.target.closest("[data-close-dialog]");
    if (close) event.preventDefault();  // Cancel is a link, for use without scripting
    if (event.target === dialog || close) dialog.close();
  });
})();
