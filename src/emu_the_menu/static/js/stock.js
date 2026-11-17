const $ = (id) => document.getElementById(id);

// Scan: take a photo of the barcode with the phone's camera app and read it here.
// The photo is read in the browser and never uploaded.
const MAX_PHOTO_SIDE = 1600;
function showScanMessage(text) {
  $("scan-message").textContent = text;
  $("scan-message").hidden = !text;
}
function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => { URL.revokeObjectURL(url); resolve(image); };
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error("unreadable image")); };
    image.src = url;
  });
}
async function readBarcode(file) {
  const image = await loadImage(file);
  const scale = Math.min(1, MAX_PHOTO_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(image.naturalWidth * scale);
  canvas.height = Math.round(image.naturalHeight * scale);
  canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
  const hints = new Map();
  hints.set(ZXing.DecodeHintType.POSSIBLE_FORMATS, [
    ZXing.BarcodeFormat.EAN_13, ZXing.BarcodeFormat.EAN_8,
    ZXing.BarcodeFormat.UPC_A, ZXing.BarcodeFormat.UPC_E,
  ]);
  hints.set(ZXing.DecodeHintType.TRY_HARDER, true);
  const reader = new ZXing.MultiFormatReader();
  reader.setHints(hints);
  const source = new ZXing.HTMLCanvasElementLuminanceSource(canvas);
  return reader.decode(new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(source))).getText();
}
$("scan-button").addEventListener("click", () => {
  if (typeof ZXing === "undefined") {
    showScanMessage("The barcode reader did not load. Type the number instead.");
    return;
  }
  $("scan-photo").click();
});
$("scan-photo").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  showScanMessage("Reading the barcode...");
  try {
    $("barcode").value = await readBarcode(file);
    showScanMessage("");
    $("lookup-button").click();
  } catch (error) {
    showScanMessage("Could not read a barcode from that photo. Try again closer, in good light, with the bars filling the photo, or type the number.");
  } finally {
    event.target.value = "";
  }
});

const suggestions = JSON.parse(document.getElementById("stock-suggestions").textContent);

// The date, the number of days/weeks/months and "No expiry" are alternatives:
// using one clears the others.
function setNoExpiry(on) {
  $("no-expiry").checked = on;
  $("expires-on").disabled = on;
  $("in-amount").disabled = on;
  $("in-unit").disabled = on;
  if (on) { $("expires-on").value = ""; $("in-amount").value = ""; }
}
$("no-expiry").addEventListener("change", (event) => setNoExpiry(event.target.checked));
$("expires-on").addEventListener("input", () => { $("in-amount").value = ""; });
$("in-amount").addEventListener("input", () => { $("expires-on").value = ""; });
setNoExpiry($("no-expiry").checked);

// Picking a remembered ingredient fills in the unit and quantity last used for it
// (the quantity only if still empty) and, if it was last added with
// "in N days/weeks/months", that shelf life too.
$("ingredient").addEventListener("input", (event) => {
  const known = suggestions[event.target.value.trim().toLowerCase()];
  if (!known) return;
  $("unit").value = known.last ? known.last.unit : known.unit;
  if (known.last && !$("quantity").value) $("quantity").value = known.last.quantity;
  if (known.shelf_life) {
    setNoExpiry(false);
    $("expires-on").value = "";
    $("in-amount").value = known.shelf_life.amount;
    $("in-unit").value = known.shelf_life.unit;
  }
});

// Click an item to adjust it in a popup. The form is fetched when it opens, so it always
// shows the item as it is now. Without scripting the link opens the same form as a page.
const itemDialog = $("item-dialog");
document.addEventListener("click", async (event) => {
  const card = event.target.closest(".results li");
  const link = card && card.querySelector("a.card-link");
  if (!link || typeof itemDialog.showModal !== "function") return;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
  event.preventDefault();
  try {
    const response = await fetch(link.dataset.panel);
    if (!response.ok) throw new Error("HTTP " + response.status);
    $("item-panel").innerHTML = await response.text();
    itemDialog.showModal();
  } catch (error) {
    window.location.href = link.href;
  }
});
itemDialog.addEventListener("click", (event) => {
  if (event.target === itemDialog || event.target.closest("[data-close-dialog]")) {
    itemDialog.close();
  }
});
