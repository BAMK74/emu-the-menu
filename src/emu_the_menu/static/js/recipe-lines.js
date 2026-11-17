// More rows on demand. Without scripting there are already a few blank ones.
(function () {
  const button = document.getElementById("add-line");
  const rows = document.getElementById("recipe-lines");
  button.hidden = false;
  button.addEventListener("click", () => {
    const row = rows.lastElementChild.cloneNode(true);
    row.querySelectorAll("input").forEach((input) => (input.value = ""));
    rows.appendChild(row);
    row.querySelector("input").focus();
  });
})();
