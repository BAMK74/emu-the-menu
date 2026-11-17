// "Read the disclaimer" opens the full text as a pop-up on the same page. Accepting stays in
// the pop-up and removes the reminder banner. Without scripting, or if the fetch fails, the
// link goes to the Disclaimer page.
(function () {
  const dialog = document.getElementById("disclaimer-dialog");
  const panel = document.getElementById("disclaimer-panel");
  if (!dialog || typeof dialog.showModal !== "function") return;
  document.addEventListener("click", async (event) => {
    const link = event.target.closest('a[href^="/disclaimer"]');
    if (!link || link.pathname !== "/disclaimer") return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    try {
      const response = await fetch("/disclaimer/panel");
      if (!response.ok) throw new Error("HTTP " + response.status);
      panel.innerHTML = await response.text();
      dialog.showModal();
      // Opening focuses the accept box at the bottom, which scrolls the text away. Start at
      // the top so it can be read from the beginning.
      dialog.scrollTop = 0;
    } catch (error) {
      window.location.href = link.href;
    }
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-close-dialog]")) dialog.close();
  });
  dialog.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-disclaimer-form]");
    if (!form) return;
    event.preventDefault();
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new URLSearchParams(new FormData(form)),
      });
      panel.innerHTML = await response.text();
      if (response.ok) document.querySelectorAll(".disclaimer-banner").forEach((b) => b.remove());
    } catch (error) {
      form.submit();
    }
  });
})();
