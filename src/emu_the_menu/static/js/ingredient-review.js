// Changing anything in a row means you have looked at it, so tick its Looks right box.
document.querySelectorAll(".review-row").forEach((row) => {
  const box = row.querySelector("[name=confirm]");
  row.addEventListener("input", (event) => {
    if (event.target !== box) box.checked = true;
  });
});
