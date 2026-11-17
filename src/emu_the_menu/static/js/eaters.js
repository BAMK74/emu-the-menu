// Show what each choice comes to for the chosen category, such as "+200 kcal (2200)".
const defaultGoals = JSON.parse(document.getElementById("default-goals").textContent);
function showGoals(form) {
  const base = defaultGoals[form.querySelector('select[name="category"]').value];
  form.querySelectorAll('select[name="calorie_adjustment"] option').forEach((option) => {
    const step = Number(option.value);
    option.textContent = step === 0
      ? `Default (${base})`
      : `${step > 0 ? "+" : "-"}${Math.abs(step)} (${base + step})`;
  });
}
document.querySelectorAll("form").forEach((form) => {
  const category = form.querySelector('select[name="category"]');
  if (!category) return;
  showGoals(form);
  category.addEventListener("change", () => showGoals(form));
});
