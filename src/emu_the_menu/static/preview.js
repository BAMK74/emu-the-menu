// Design preview: five versions of Modern Magazine, each in every season and two festive overlays.
// A link can set the look for one visit: /menu?design=mag-kinetic&season=winter&festive=christmas&motion=off
// The hub at /static/design.html shows a phone and a computer side by side.
(function () {
  const KEY = "emu-design-preview-3";
  const DESIGNS = {
    "mag-curtain": { name: "A  Curtain Call", tone: "light" },
    "mag-parallax": { name: "B  Parallax Newsprint", tone: "light" },
    "mag-kinetic": { name: "C  Kinetic Type", tone: "light" },
    "mag-horizon": { name: "D  Horizon (night kitchen)", tone: "light" },
  };
  const SEASONS = { auto: "Follow the date", spring: "Spring", summer: "Summer", autumn: "Autumn", winter: "Winter" };
  const FESTIVE = { auto: "Follow the date", off: "None", halloween: "Halloween", christmas: "Christmas" };
  const HEMI = { north: "Northern", south: "Southern" };
  const MOTION = { on: "On", off: "Off" };
  const COPY = {
    spring: ["Fresh, crisp and green", "Asparagus, peas and radishes are at their best. What will you cook this week?"],
    summer: ["Light, bright and quick", "Ripe tomatoes, sweet corn and dinners that taste like sunshine."],
    autumn: ["Warm bowls and slow simmers", "Pumpkin, apples and mushrooms are in season. Time for something comforting."],
    winter: ["Cosy suppers and big pots", "Soups, roasts and fresh bread to keep the house warm."],
    halloween: ["Spooky snacks and pumpkin everything", "Carve one, cook one. What will you make?"],
    christmas: ["Bake, roast and share", "Gingerbread, spice and a table full of people."],
  };
  // Real photos in round tiles (see /static/photos/credits.html). Position and size are percentages of the box.
  const PHOTOS = {
    spring: ["asparagus", "radish", "peas", "strawberry", "egg", "basil"],
    summer: ["corn", "tomato", "lemon", "watermelon", "peach", "basil"],
    autumn: ["pumpkin", "mushroom", "apple", "pear", "carrot", "rosemary"],
    winter: ["soup", "bread", "orange", "garlic", "carrot", "rosemary"],
    halloween: ["pumpkin", "apple", "mushroom", "soup", "carrot", "pear"],
    christmas: ["gingerbread", "cinnamon", "orange", "anise", "rosemary", "bread"],
  };
  const TILES = [[0, 14, 31, -4], [21, 0, 34, 3], [44, 22, 29, -2], [63, 2, 28, 4], [77, 34, 25, -3], [40, 50, 17, 5]];
  const DEPTH = [20, 120, 0, 70, 150, -50];
  const SPEEDS = [0.35, 0.7, 1.0, 0.5, 1.2, 0.6];
  const root = document.documentElement;
  const q = new URLSearchParams(location.search);

  const defaults = () => ({ design: "mag-curtain", season: "auto", festive: "auto", hemi: "north", motion: "on", open: false });
  const load = () => { try { return { ...defaults(), ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch (e) { return defaults(); } };
  const save = (s) => { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* private window */ } };

  function seasonOf(date, hemi) {
    const m = date.getMonth();
    const north = m >= 2 && m <= 4 ? "spring" : m >= 5 && m <= 7 ? "summer" : m >= 8 && m <= 10 ? "autumn" : "winter";
    return hemi === "north" ? north : { spring: "autumn", summer: "winter", autumn: "spring", winter: "summer" }[north];
  }
  function festiveOf(date) {
    const m = date.getMonth(), d = date.getDate();
    if (m === 9 && d >= 24) return "halloween";
    if (m === 11 && d >= 15 && d <= 26) return "christmas";
    return "off";
  }
  function look(s) {
    const now = new Date();
    const season = s.season === "auto" ? seasonOf(now, s.hemi) : s.season;
    const festive = s.festive === "auto" ? festiveOf(now) : s.festive;
    return { season, festive, scene: festive !== "off" ? festive : season };
  }
  function apply(s) {
    const l = look(s);
    root.dataset.design = DESIGNS[s.design] ? s.design : "mag-curtain";
    root.dataset.tone = DESIGNS[root.dataset.design].tone;
    root.dataset.season = l.season;
    root.dataset.festive = l.festive;
    root.dataset.motion = s.motion;
    if (q.get("embed")) root.dataset.embed = "1";
    paintHero(l);
  }
  function words(el, text) {
    el.textContent = "";
    text.split(" ").forEach((w, i) => {
      const outer = document.createElement("span");
      outer.className = "w";
      const inner = document.createElement("span");
      inner.style.setProperty("--w", String(i));
      inner.textContent = w;
      outer.append(inner);
      el.append(outer);
    });
  }
  function paintHero(l) {
    const hero = document.getElementById("hero");
    if (!hero) return;
    const art = document.getElementById("hero-art");
    if (art && art.dataset.key !== l.scene) {
      art.dataset.key = l.scene;
      art.classList.remove("go");
      art.textContent = "";
      const ready = PHOTOS[l.scene].map((name, i) => {
        const [x, y, size, rot] = TILES[i];
        const img = document.createElement("img");
        img.src = "/static/photos/" + name + ".jpg";
        img.alt = "";
        img.width = 320; img.height = 320;
        img.style.cssText = `left:${x}%;top:${y}%;width:${size}%;--i:${i};--r:${rot}deg;--sp:${SPEEDS[i]};--z:${DEPTH[i]}px` + (DEPTH[i] < 0 ? ";--far:1" : "");
        art.append(img);
        return img.decode().catch(() => {});
      });
      // Start the motion once every photo is decoded (or after 1.5 s), so nothing pops in half drawn.
      Promise.race([Promise.all(ready), new Promise((r) => setTimeout(r, 1500))]).then(() => art.classList.add("go"));
    }
    const [line, sub] = COPY[l.scene];
    const lineEl = document.getElementById("hero-line");
    const lineKey = root.dataset.design + "|" + line;
    if (lineEl.dataset.key !== lineKey) {
      lineEl.dataset.key = lineKey;
      if (root.dataset.design === "mag-kinetic" || root.dataset.design === "mag-horizon") words(lineEl, line); else lineEl.textContent = line;
    }
    document.getElementById("hero-sub").textContent = sub;
    const path = location.pathname;
    hero.classList.toggle("compact", !(path === "/" || path === "/menu" || path === "/menus"));
    document.getElementById("hero-cta").hidden = path === "/menu";
  }
  let state = load();
  for (const k of ["design", "season", "festive", "hemi", "motion"]) if (q.get(k)) state[k] = q.get(k);
  document.addEventListener("DOMContentLoaded", () => {
    paintHero(look(state));
    // For checking a scrolled state: ?scroll=600
    const y = parseInt(q.get("scroll") || "", 10);
    if (y) window.addEventListener("load", () => {
      window.scrollTo(0, y);
      // For checking the parallax without a screenshot: ?scroll=300&dbg=1 writes the drift onto <html>.
      if (q.get("dbg")) setTimeout(() => {
        const copy = document.querySelector(".hero-copy");
        const cs = getComputedStyle(copy); root.dataset.dbg = "y=" + Math.round(window.scrollY) + " translate=" + cs.translate + " name=" + cs.animationName + " tl=" + cs.animationTimeline + " supports=" + CSS.supports("animation-timeline", "scroll()") + " ua=" + navigator.userAgent.split("Chrome/")[1];
      }, 400);
    });
  });
  apply(state);

  // Horizon: the scene tilts towards the pointer, eased so it glides. Touch screens keep the idle sway.
  (function () {
    let tx = 0, ty = 0, cx = 0, cy = 0, run = false;
    function step() {
      const hero = document.getElementById("hero");
      if (!hero) { run = false; return; }
      cx += (tx - cx) * 0.08; cy += (ty - cy) * 0.08;
      hero.style.setProperty("--px", cx.toFixed(3)); hero.style.setProperty("--py", cy.toFixed(3));
      if (Math.abs(tx - cx) + Math.abs(ty - cy) > 0.002) requestAnimationFrame(step); else run = false;
    }
    window.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "mouse" || root.dataset.design !== "mag-horizon" || root.dataset.motion === "off") return;
      if (!matchMedia("(prefers-reduced-motion: no-preference)").matches) return;
      const hero = document.getElementById("hero");
      if (!hero) return;
      if (!hero.classList.contains("pt")) {
        const cs = getComputedStyle(hero);
        cx = parseFloat(cs.getPropertyValue("--px")) || 0;
        cy = parseFloat(cs.getPropertyValue("--py")) || 0;
        hero.classList.add("pt");
      }
      tx = (e.clientX / innerWidth - 0.5) * 2; ty = (e.clientY / innerHeight - 0.5) * 2;
      if (!run) { run = true; requestAnimationFrame(step); }
    }, { passive: true });
  })();

  window.addEventListener("DOMContentLoaded", () => {
    if (q.get("embed")) return;
    const toggle = document.createElement("button");
    toggle.id = "dp-toggle"; toggle.type = "button"; toggle.textContent = "Design preview";
    toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-controls", "dp-panel");
    const panel = document.createElement("div");
    panel.id = "dp-panel"; panel.hidden = true; panel.setAttribute("role", "region"); panel.setAttribute("aria-label", "Design preview");
    const select = (label, options, key) => {
      const wrap = document.createElement("label");
      wrap.textContent = label;
      const sel = document.createElement("select");
      sel.dataset.key = key;
      for (const [value, text] of Object.entries(options)) sel.add(new Option(text, value));
      wrap.append(sel);
      return wrap;
    };
    const h2 = document.createElement("h2"); h2.textContent = "Design preview";
    const note = document.createElement("p"); note.className = "dp-note";
    note.textContent = "Safety colours (amber, red) never change with a season.";
    const hub = document.createElement("a"); hub.href = "/static/design.html"; hub.textContent = "Phone and computer side by side";
    panel.append(h2, select("Version", Object.fromEntries(Object.entries(DESIGNS).map(([k, v]) => [k, v.name])), "design"),
      select("Season", SEASONS, "season"), select("Festive", FESTIVE, "festive"), select("Hemisphere", HEMI, "hemi"),
      select("Motion", MOTION, "motion"), note, hub);
    document.body.append(panel, toggle);
    const sync = () => {
      for (const sel of panel.querySelectorAll("select")) sel.value = state[sel.dataset.key];
      panel.hidden = !state.open;
      toggle.setAttribute("aria-expanded", String(state.open));
    };
    const change = (patch) => {
      state = { ...state, ...patch }; save(state); sync();
      // A different version or season replays its movement from the start.
      if ("design" in patch || "season" in patch || "festive" in patch) location.reload(); else apply(state);
    };
    toggle.addEventListener("click", () => { state = { ...state, open: !state.open }; save(state); sync(); });
    panel.addEventListener("change", (e) => { if (e.target.dataset.key) change({ [e.target.dataset.key]: e.target.value }); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.open) { state = { ...state, open: false }; save(state); sync(); } });
    sync();
  });
})();
