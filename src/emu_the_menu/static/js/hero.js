// The photos start moving together once every one is decoded (or after 1.5 s).
(function () {
  const art = document.getElementById("hero-art");
  if (!art) return;
  const ready = Array.from(art.querySelectorAll("img"), (img) => img.decode().catch(() => {}));
  Promise.race([Promise.all(ready), new Promise((r) => setTimeout(r, 1500))]).then(() => art.classList.add("go"));
})();
