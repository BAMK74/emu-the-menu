// Shared by every page, and by forms that arrive inside a popup.
// A form marked data-confirm asks before it is sent.
document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-confirm]");
  if (form && !confirm(form.dataset.confirm)) event.preventDefault();
});
// Temperature limits in Settings: a box marked data-other-unit="id" shows its figure in the other
// unit inside #id as you type, and switching the unit converts the boxes.
const round1 = (x) => String(Math.round(x * 10) / 10);
const convert = (text, from) => {
  const n = parseFloat(text.replace(",", "."));
  if (!Number.isFinite(n)) return null;
  return from === "C" ? n * 9 / 5 + 32 : (n - 32) * 5 / 9;
};
const showOther = (box, unit) => {
  const out = document.getElementById(box.dataset.otherUnit);
  const v = convert(box.value, unit);
  if (out) out.textContent = v === null ? "?" : round1(v);
};
document.addEventListener("input", (event) => {
  const box = event.target.closest("input[data-other-unit]");
  const group = box && box.closest("[data-temp-unit]");
  if (group) showOther(box, group.dataset.tempUnit);
});
document.addEventListener("change", (event) => {
  const select = event.target.closest("select[data-temp-unit-select]");
  const group = select && select.closest("[data-temp-unit]");
  if (!group || select.value === group.dataset.tempUnit) return;
  const from = group.dataset.tempUnit, to = select.value;
  group.dataset.tempUnit = to;
  group.querySelectorAll("input[data-other-unit]").forEach((box) => {
    const v = convert(box.value, from);
    if (v !== null) box.value = round1(v);
    showOther(box, to);
  });
  group.querySelectorAll("[data-unit-name]").forEach((el) => { el.textContent = to; });
  group.querySelectorAll("[data-other-unit-name]").forEach((el) => { el.textContent = from; });
});
// Ticking "No expiry" switches the date off and clears it.
document.addEventListener("change", (event) => {
  const box = event.target.closest("input[data-no-expiry]");
  if (!box) return;
  const date = box.form.querySelector('input[name="expires_on"]');
  date.disabled = box.checked;
  if (box.checked) date.value = "";
});
// Enter in the barcode box looks it up instead of saving the form.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || !event.target.matches("[data-barcode]")) return;
  event.preventDefault();
  event.target.form.querySelector("[data-lookup-calories]").click();
});
// Look up calories from a barcode. It fills in only the calories box and never saves.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-lookup-calories]");
  if (!button) return;
  event.preventDefault();
  const form = button.form;
  const note = form.querySelector("[data-lookup-note]");
  note.hidden = false;
  note.textContent = "Looking up...";
  try {
    const response = await fetch(button.dataset.url, {
      method: "POST",
      // Not a whole form, so the page's CSRF token goes in a header (see auth.guard).
      headers: { "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content },
      body: new URLSearchParams({ barcode: form.querySelector("[data-barcode]").value }),
    });
    const data = await response.json();
    note.textContent = data.message;
    if (data.ok) form.querySelector('[name="kcal"]').value = data.kcal;
  } catch (error) {
    note.textContent = "Could not look it up. Enter the calories by hand.";
  }
});
