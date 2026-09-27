// OCR bounding-box viewer: renders a PDF page with pdf.js and overlays
// word/line/block boxes from an OCR JSON file so mis-recognitions are easy to spot.

pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

const el = (id) => document.getElementById(id);

const state = {
  pdfDoc: null,
  pdfPageNum: 1,
  pdfPageCount: 0,
  pdfFile: null,
  ocrPages: [],        // array of {page_number, blocks, width, height}
  ocrPageIndex: -1,    // index into ocrPages currently shown
  ocrFile: null,
  zoom: 1.2,
  calib: {},           // per ocr page_number -> {ocrWidth, ocrHeight, offsetX, offsetY}
  renderTask: null,
};

const pdfCanvas = el("pdfCanvas");
const overlayCanvas = el("overlayCanvas");
const tooltip = el("tooltip");

// ============================================================
// Start screen: drag & drop + click-to-browse for both files
// ============================================================

function setupDropzone(zoneId, inputId, kind, onFile) {
  const zone = el(zoneId);
  const input = el(inputId);

  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) onFile(file);
  });

  ["dragenter", "dragover"].forEach((evt) =>
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.add("dragover");
    })
  );
  ["dragleave", "dragend"].forEach((evt) =>
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
    })
  );
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    e.stopPropagation();
    zone.classList.remove("dragover");
    const file = e.dataTransfer.files[0];
    if (file) onFile(file);
  });
}

setupDropzone("pdfDropzone", "pdfInput", "pdf", handlePdfFile);
setupDropzone("jsonDropzone", "jsonInput", "json", handleJsonFile);

el("changeFilesBtn").addEventListener("click", () => {
  el("app").classList.add("hidden");
  el("startScreen").classList.remove("hidden");
});

function maybeEnterViewer() {
  if (state.pdfDoc && state.ocrPages.length) {
    el("startScreen").classList.add("hidden");
    el("app").classList.remove("hidden");
    syncPdfPageFromOcr();
    render();
  }
}

// ---------- File loading ----------

async function handlePdfFile(file) {
  try {
    const buf = await file.arrayBuffer();
    state.pdfDoc = await pdfjsLib.getDocument({ data: buf }).promise;
    state.pdfPageCount = state.pdfDoc.numPages;
    state.pdfPageNum = 1;
    state.pdfFile = file;
    el("pdfDropzone").classList.add("loaded");
    el("pdfFileLabel").textContent = `${file.name} (${state.pdfPageCount} pages)`;
    setStatus("");
    maybeEnterViewer();
  } catch (err) {
    setStatus("Failed to load PDF: " + err.message, true);
  }
}

async function handleJsonFile(file) {
  const text = await file.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch (err) {
    setStatus("Failed to parse JSON: " + err.message, true);
    return;
  }
  state.ocrPages = Array.isArray(data.pages) ? data.pages : [];
  state.ocrFile = file;
  populateOcrPageSelect();
  if (state.ocrPages.length) {
    state.ocrPageIndex = 0;
    const firstPageNumber = state.ocrPages[0].page_number ?? 1;
    el("pageOffset").value = 1 - firstPageNumber;
  }
  el("jsonDropzone").classList.add("loaded");
  el("jsonFileLabel").textContent = `${file.name} (${state.ocrPages.length} pages)`;
  setStatus("");
  maybeEnterViewer();
}

function setStatus(msg, isError) {
  const s = el("loadStatus");
  s.textContent = msg;
  s.style.color = isError ? "var(--danger)" : "var(--text-muted)";
}

function populateOcrPageSelect() {
  const sel = el("ocrPageSelect");
  sel.innerHTML = "";
  state.ocrPages.forEach((p, i) => {
    const opt = document.createElement("option");
    opt.value = i;
    opt.textContent = `Page ${p.page_number}`;
    sel.appendChild(opt);
  });
}

// ---------- Navigation ----------

el("pdfPrev").addEventListener("click", () => {
  if (state.pdfPageNum > 1) {
    state.pdfPageNum--;
    syncOcrPageFromPdf();
    render();
  }
});

el("pdfNext").addEventListener("click", () => {
  if (state.pdfPageNum < state.pdfPageCount) {
    state.pdfPageNum++;
    syncOcrPageFromPdf();
    render();
  }
});

el("ocrPageSelect").addEventListener("change", (e) => {
  state.ocrPageIndex = parseInt(e.target.value, 10);
  if (el("linkPages").checked) syncPdfPageFromOcr();
  render();
});

el("pageOffset").addEventListener("input", () => {
  if (el("linkPages").checked) syncPdfPageFromOcr();
  render();
});

function syncPdfPageFromOcr() {
  if (state.ocrPageIndex < 0 || !state.pdfDoc) return;
  const ocrPage = state.ocrPages[state.ocrPageIndex];
  const offset = parseInt(el("pageOffset").value || "0", 10);
  const target = (ocrPage.page_number ?? 1) + offset;
  if (target >= 1 && target <= state.pdfPageCount) {
    state.pdfPageNum = target;
  }
}

// When the user manually flips PDF pages, try to find a matching OCR page too.
function syncOcrPageFromPdf() {
  if (!el("linkPages").checked || !state.ocrPages.length) return;
  const offset = parseInt(el("pageOffset").value || "0", 10);
  const wantedOcrPageNumber = state.pdfPageNum - offset;
  const idx = state.ocrPages.findIndex((p) => p.page_number === wantedOcrPageNumber);
  if (idx >= 0) {
    state.ocrPageIndex = idx;
    el("ocrPageSelect").value = idx;
  }
}

el("zoom").addEventListener("input", (e) => {
  state.zoom = parseFloat(e.target.value);
  el("zoomLabel").textContent = state.zoom.toFixed(2) + "x";
  render();
});

// ---------- Calibration ----------

["ocrWidth", "ocrHeight", "offsetX", "offsetY"].forEach((id) => {
  el(id).addEventListener("input", () => {
    saveCalibFromInputs();
    drawOverlay();
  });
});

el("autoFit").addEventListener("click", () => {
  autoFitCalibration(true);
  drawOverlay();
});

el("resetCalib").addEventListener("click", () => {
  autoFitCalibration(true);
  el("offsetX").value = 0;
  el("offsetY").value = 0;
  saveCalibFromInputs();
  drawOverlay();
});

function currentOcrPage() {
  return state.ocrPageIndex >= 0 ? state.ocrPages[state.ocrPageIndex] : null;
}

function calibKey() {
  const p = currentOcrPage();
  return p ? p.page_number : null;
}

function loadCalibToInputs() {
  const key = calibKey();
  if (key == null) return;
  const c = state.calib[key];
  if (c) {
    el("ocrWidth").value = c.ocrWidth;
    el("ocrHeight").value = c.ocrHeight;
    el("offsetX").value = c.offsetX;
    el("offsetY").value = c.offsetY;
  } else {
    autoFitCalibration();
  }
}

function saveCalibFromInputs() {
  const key = calibKey();
  if (key == null) return;
  state.calib[key] = {
    ocrWidth: parseFloat(el("ocrWidth").value) || 1,
    ocrHeight: parseFloat(el("ocrHeight").value) || 1,
    offsetX: parseFloat(el("offsetX").value) || 0,
    offsetY: parseFloat(el("offsetY").value) || 0,
  };
}

// Prefer the page's own width/height from the JSON. Fall back to estimating
// from the max extent of word boxes if those fields are missing.
function autoFitCalibration(forceRecompute) {
  const page = currentOcrPage();
  if (!page) return;

  let ocrWidth = page.width;
  let ocrHeight = page.height;

  if (!ocrWidth || !ocrHeight) {
    let maxX = 0, maxY = 0;
    for (const block of page.blocks || []) {
      for (const line of block.lines || []) {
        for (const word of line.words || []) {
          const bb = word.bounding_box;
          if (!bb) continue;
          maxX = Math.max(maxX, bb.x + bb.width);
          maxY = Math.max(maxY, bb.y + bb.height);
        }
      }
    }
    ocrWidth = maxX * 1.02;
    ocrHeight = maxY * 1.02;
  }
  if (!ocrWidth || !ocrHeight) return;

  el("ocrWidth").value = Math.round(ocrWidth);
  el("ocrHeight").value = Math.round(ocrHeight);
  if (forceRecompute || !state.calib[calibKey()]) {
    el("offsetX").value = 0;
    el("offsetY").value = 0;
  }
  saveCalibFromInputs();
}

// ---------- Display toggles ----------

["showWordBoxes", "showLineBoxes", "showBlockBoxes", "showText", "applyRotation", "onlyLowConf"].forEach((id) => {
  el(id).addEventListener("change", drawOverlay);
});

el("confThreshold").addEventListener("input", (e) => {
  el("confThresholdLabel").textContent = parseFloat(e.target.value).toFixed(2);
  drawOverlay();
});

// ---------- Rendering ----------

async function render() {
  el("pdfPageLabel").textContent = state.pdfDoc
    ? `${state.pdfPageNum} / ${state.pdfPageCount}`
    : "-";

  if (state.pdfDoc) {
    await renderPdfPage();
  }
  loadCalibToInputs();
  drawOverlay();
  updateStats();
}

async function renderPdfPage() {
  if (state.renderTask) {
    try { state.renderTask.cancel(); } catch (e) {}
  }
  const page = await state.pdfDoc.getPage(state.pdfPageNum);
  const viewport = page.getViewport({ scale: state.zoom });

  pdfCanvas.width = viewport.width;
  pdfCanvas.height = viewport.height;
  overlayCanvas.width = viewport.width;
  overlayCanvas.height = viewport.height;

  const ctx = pdfCanvas.getContext("2d");
  state.renderTask = page.render({ canvasContext: ctx, viewport });
  try {
    await state.renderTask.promise;
  } catch (e) {
    if (e && e.name !== "RenderingCancelledException") throw e;
  }
}

// Confidence ramp: flagged (vermilion) -> uncertain (ochre) -> confident (sap),
// matching the "Marks" legend colors instead of a raw traffic-light hue sweep.
const CONF_LOW = [171, 58, 44];
const CONF_MID = [169, 130, 44];
const CONF_HIGH = [77, 124, 80];

function lerp(a, b, t) { return a + (b - a) * t; }

function confColor(conf) {
  const t = Math.max(0, Math.min(1, conf));
  const [from, to, localT] = t < 0.5 ? [CONF_LOW, CONF_MID, t / 0.5] : [CONF_MID, CONF_HIGH, (t - 0.5) / 0.5];
  const r = Math.round(lerp(from[0], to[0], localT));
  const g = Math.round(lerp(from[1], to[1], localT));
  const b = Math.round(lerp(from[2], to[2], localT));
  return `rgb(${r}, ${g}, ${b})`;
}

function confClass(conf) {
  if (conf >= 0.95) return "conf-high";
  if (conf >= 0.8) return "conf-mid";
  return "conf-low";
}

const BLOCK_COLOR = "#8a6023";
const LINE_COLOR = "#3d5789";

function getTransform() {
  const key = calibKey();
  const c = state.calib[key];
  const canvasW = overlayCanvas.width;
  const canvasH = overlayCanvas.height;
  if (!c || !c.ocrWidth || !c.ocrHeight) {
    return { sx: 1, sy: 1, ox: 0, oy: 0 };
  }
  return {
    sx: canvasW / c.ocrWidth,
    sy: canvasH / c.ocrHeight,
    ox: c.offsetX * (canvasW / c.ocrWidth),
    oy: c.offsetY * (canvasH / c.ocrHeight),
  };
}

// The JSON stores box rotation in degrees, not radians.
function boxAngleRad(bb) {
  return ((bb.angle || 0) * Math.PI) / 180;
}

// Compute the four corners of a (possibly rotated) box, in OCR space.
function boxCorners(bb) {
  const angle = boxAngleRad(bb);
  const cx = bb.x + bb.width / 2;
  const cy = bb.y + bb.height / 2;
  const hw = bb.width / 2;
  const hh = bb.height / 2;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const pts = [
    [-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh],
  ];
  return pts.map(([px, py]) => [
    cx + px * cos - py * sin,
    cy + px * sin + py * cos,
  ]);
}

// Axis-aligned bounding box (in OCR space) enclosing all lines of a block.
function blockBoundingBox(block) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const line of block.lines || []) {
    if (!line.bounding_box) continue;
    for (const [x, y] of boxCorners(line.bounding_box)) {
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }
  }
  if (minX === Infinity) return null;
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY, angle: 0 };
}

// Flat list of drawable boxes for the current OCR page, used for both
// drawing and hit-testing on hover.
let currentBoxes = [];

function collectBoxes() {
  const page = currentOcrPage();
  currentBoxes = [];
  if (!page) return;

  const threshold = parseFloat(el("confThreshold").value);
  const onlyLow = el("onlyLowConf").checked;
  const showBlock = el("showBlockBoxes").checked;
  const showLine = el("showLineBoxes").checked;
  const showWord = el("showWordBoxes").checked;

  for (const block of page.blocks || []) {
    if (showBlock) {
      const bb = blockBoundingBox(block);
      if (bb) {
        currentBoxes.push({ type: "block", text: block.block_type || "block", confidence: null, bb });
      }
    }
    for (const line of block.lines || []) {
      if (showLine && line.bounding_box) {
        currentBoxes.push({ type: "line", text: line.text, confidence: null, bb: line.bounding_box });
      }
      for (const word of line.words || []) {
        if (!word.bounding_box) continue;
        if (onlyLow && word.confidence >= threshold) continue;
        if (showWord) {
          currentBoxes.push({ type: "word", text: word.text, confidence: word.confidence, bb: word.bounding_box });
        }
      }
    }
  }
}

function drawOverlay() {
  const ctx = overlayCanvas.getContext("2d");
  ctx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
  if (!currentOcrPage()) return;

  collectBoxes();
  const { sx, sy, ox, oy } = getTransform();
  const applyRotation = el("applyRotation").checked;
  const showText = el("showText").checked;

  // draw order: block (back) -> line -> word (front)
  const order = { block: 0, line: 1, word: 2 };
  const boxesToDraw = [...currentBoxes].sort((a, b) => order[a.type] - order[b.type]);

  for (const box of boxesToDraw) {
    const bb = box.bb;
    const x = bb.x * sx + ox;
    const y = bb.y * sy + oy;
    const w = bb.width * sx;
    const h = bb.height * sy;
    const angle = applyRotation ? boxAngleRad(bb) : 0;
    const cx = x + w / 2;
    const cy = y + h / 2;

    let color;
    if (box.type === "block") color = BLOCK_COLOR;
    else if (box.type === "line") color = LINE_COLOR;
    else color = confColor(box.confidence ?? 1);

    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(angle);
    ctx.strokeStyle = color;
    if (box.type === "block") {
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
    } else if (box.type === "line") {
      ctx.lineWidth = 1.25;
      ctx.setLineDash([3, 3]);
    } else {
      ctx.lineWidth = 1.5;
      ctx.setLineDash([]);
    }
    ctx.strokeRect(-w / 2, -h / 2, w, h);
    ctx.restore();

    if (showText && box.type === "word" && h > 6) {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(angle);
      ctx.fillStyle = "rgba(241,239,230,0.88)";
      ctx.fillRect(-w / 2, -h / 2, w, h);
      ctx.fillStyle = "#211f1a";
      ctx.font = `${Math.max(9, Math.min(h * 0.7, 22))}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(box.text, 0, 0, w * 0.98);
      ctx.restore();
    }
  }
}

// ---------- Hover tooltip ----------

overlayCanvas.addEventListener("mousemove", (e) => {
  if (!currentBoxes.length) {
    tooltip.classList.add("hidden");
    return;
  }
  const rect = overlayCanvas.getBoundingClientRect();
  const px = e.clientX - rect.left;
  const py = e.clientY - rect.top;
  const { sx, sy, ox, oy } = getTransform();
  const applyRotation = el("applyRotation").checked;

  // priority: word > line > block, so smaller/more specific boxes win
  const priority = { word: 0, line: 1, block: 2 };
  const sorted = [...currentBoxes].sort((a, b) => priority[a.type] - priority[b.type]);

  let hit = null;
  for (const box of sorted) {
    const bb = box.bb;
    const x = bb.x * sx + ox;
    const y = bb.y * sy + oy;
    const w = bb.width * sx;
    const h = bb.height * sy;
    const angle = applyRotation ? boxAngleRad(bb) : 0;
    const cx = x + w / 2;
    const cy = y + h / 2;

    const dx = px - cx;
    const dy = py - cy;
    const cos = Math.cos(-angle);
    const sin = Math.sin(-angle);
    const lx = dx * cos - dy * sin;
    const ly = dx * sin + dy * cos;

    if (Math.abs(lx) <= w / 2 && Math.abs(ly) <= h / 2) {
      hit = box;
      break;
    }
  }

  if (hit) {
    const confText =
      hit.confidence == null
        ? ""
        : `<br/><span class="${confClass(hit.confidence)}">confidence: ${(hit.confidence * 100).toFixed(1)}%</span>`;
    const label = hit.type === "block" ? `[block: ${escapeHtml(hit.text)}]` : escapeHtml(hit.text);
    tooltip.innerHTML = `<b>${label}</b>${confText}`;
    tooltip.style.left = e.clientX + 14 + "px";
    tooltip.style.top = e.clientY + 14 + "px";
    tooltip.classList.remove("hidden");
  } else {
    tooltip.classList.add("hidden");
  }
});

overlayCanvas.addEventListener("mouseleave", () => {
  tooltip.classList.add("hidden");
});

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------- Stats ----------

function updateStats() {
  const page = currentOcrPage();
  const statsEl = el("stats");
  if (!page) {
    statsEl.textContent = "No OCR page loaded.";
    return;
  }
  let wordCount = 0, lineCount = 0, blockCount = 0, sumConf = 0, lowConf = 0;
  const threshold = parseFloat(el("confThreshold").value);
  for (const block of page.blocks || []) {
    blockCount++;
    for (const line of block.lines || []) {
      lineCount++;
      for (const word of line.words || []) {
        wordCount++;
        sumConf += word.confidence ?? 0;
        if ((word.confidence ?? 1) < threshold) lowConf++;
      }
    }
  }
  const avg = wordCount ? (sumConf / wordCount) * 100 : 0;
  statsEl.innerHTML = `
    Page number: <b>${page.page_number}</b><br/>
    Blocks: <b>${blockCount}</b> &nbsp; Lines: <b>${lineCount}</b> &nbsp; Words: <b>${wordCount}</b><br/>
    Avg confidence: <b>${avg.toFixed(1)}%</b><br/>
    Below threshold: <b>${lowConf}</b>
  `;
}
