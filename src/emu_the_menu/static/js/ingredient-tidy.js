// Beside each proposed change: Reset puts its suggested name back and ticks it, Cancel leaves
// it out (unticks it), and Save makes just that change.
(function () {
  const mark = (row) => row.classList.toggle("skipped", !row.querySelector("[name=apply]").checked);
  document.querySelectorAll(".tidy-item").forEach((row) => {
    row.querySelector("[name=apply]").addEventListener("change", () => mark(row));
  });
  document.addEventListener("click", (event) => {
    const reset = event.target.closest("[data-tidy-reset]");
    const cancel = event.target.closest("[data-tidy-cancel]");
    const row = (reset || cancel) && (reset || cancel).closest(".tidy-item");
    if (!row) return;
    const box = row.querySelector("[name=apply]");
    const name = row.querySelector(".tidy-name");
    if (reset) {
      name.value = name.defaultValue;
      box.checked = true;
    } else {
      box.checked = false;
    }
    mark(row);
  });
})();
