// Kahie PWA — record, confirm, dashboard, statement. Offline queue for voice notes.
const $ = (s) => document.querySelector(s);
const fmt = (n) => new Intl.NumberFormat("fr-FR").format(Math.round(n)) + " Ar";
let current = null; // pending plan shown on the confirmation card

// ---------------------------------------------------------------- navigation
function show(name) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.toggle("active", s.id === "screen-" + name));
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.screen === name));
  if (name === "dash") loadDash();
  if (name === "statement") loadStatement();
}
document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => show(b.dataset.screen)));

function toast(msg, action) {
  const t = $("#toast");
  t.innerHTML = `<span>${esc(msg)}</span>` + (action ? `<button>${esc(action.label)}</button>` : "");
  t.hidden = false;
  if (action) t.querySelector("button").onclick = () => { t.hidden = true; action.run(); };
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 6000);
}

// ---------------------------------------------------------------- voice (speech synthesis)
// Kahie reads its answers and the confirmation card aloud. Numbers come from the server already
// in Malagasy words ("roa arivo sy valonjato"). No Malagasy voice ships with browsers: we prefer one
// if the phone has it, else a French voice (closest spelling-to-sound), else the default voice.
let voiceOn = true;
try { voiceOn = localStorage.getItem("kahie-voice") !== "off"; } catch {}
function renderVoice() { $("#voice").textContent = voiceOn ? "🔊" : "🔇"; $("#voice").classList.toggle("off", !voiceOn); }
$("#voice").onclick = () => {
  voiceOn = !voiceOn;
  try { localStorage.setItem("kahie-voice", voiceOn ? "on" : "off"); } catch {}
  if (!voiceOn && "speechSynthesis" in window) speechSynthesis.cancel();
  renderVoice();
};
function pickVoice() {
  const vs = ("speechSynthesis" in window) ? speechSynthesis.getVoices() : [];
  return vs.find((v) => /^mg/i.test(v.lang)) || vs.find((v) => /^fr/i.test(v.lang)) || null;
}
function speak(text) {
  if (!voiceOn || !text || !("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  const v = pickVoice();
  if (v) { u.voice = v; u.lang = v.lang; } else { u.lang = "fr-FR"; }
  u.rate = 0.95;
  speechSynthesis.speak(u);
}
if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = () => {};
renderVoice();

// ---------------------------------------------------------------- offline queue (IndexedDB)
const idb = new Promise((res, rej) => {
  const r = indexedDB.open("kahie", 1);
  r.onupgradeneeded = () => r.result.createObjectStore("queue", { autoIncrement: true });
  r.onsuccess = () => res(r.result);
  r.onerror = () => rej(r.error);
});
async function enqueue(blob) {
  const db = await idb;
  db.transaction("queue", "readwrite").objectStore("queue").add({ blob, at: Date.now() });
  renderQueue();
}
async function queued() {
  const db = await idb;
  return new Promise((res) => {
    const out = [];
    db.transaction("queue").objectStore("queue").openCursor().onsuccess = (e) => {
      const c = e.target.result;
      if (c) { out.push({ key: c.key, ...c.value }); c.continue(); } else res(out);
    };
  });
}
async function dequeue(key) {
  const db = await idb;
  db.transaction("queue", "readwrite").objectStore("queue").delete(key);
}
async function renderQueue() {
  const q = await queued();
  $("#queue").hidden = q.length === 0;
  $("#queue").textContent = `${q.length} feo miandry ny réseau (en attente)`;
}
async function flushQueue() {
  if (!navigator.onLine) return;
  for (const item of await queued()) {
    try { await sendVoice(item.blob); await dequeue(item.key); break; } // one at a time: each needs a confirmation
    catch { break; }
  }
  renderQueue();
}
function netState() {
  $("#net").classList.toggle("off", !navigator.onLine);
  if (navigator.onLine) flushQueue();
}
addEventListener("online", netState);
addEventListener("offline", netState);

// ---------------------------------------------------------------- recording
let recorder, chunks = [];
$("#mic").onclick = async () => {
  if (recorder && recorder.state === "recording") return recorder.stop();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recorder = new MediaRecorder(stream);
    chunks = [];
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      $("#mic").classList.remove("rec");
      const blob = new Blob(chunks, { type: recorder.mimeType });
      if (!navigator.onLine) { await enqueue(blob); $("#rec-state").textContent = "Voatahiry, halefa rehefa misy réseau"; return; }
      await sendVoice(blob);
    };
    recorder.start();
    $("#mic").classList.add("rec");
    $("#rec-state").textContent = "Mihaino… (tsindrio indray raha vita)";
  } catch {
    $("#rec-state").textContent = "Tsy azo ampiasaina ny micro";
  }
};

async function sendVoice(blob, text) {
  $("#rec-state").textContent = "Mandinika…";
  const fd = new FormData();
  if (blob) fd.append("audio", blob, "note.webm");
  if (text) fd.append("text", text);
  const r = await fetch("api/voice", { method: "POST", body: fd });
  if (!r.ok) { $("#rec-state").textContent = "Nisy olana, andramo indray"; throw new Error(await r.text()); }
  const res = await r.json();
  if (res.kind === "answer") showAnswer(res); else renderCard(res);
  $("#rec-state").textContent = "Vonona";
}

// ---------------------------------------------------------------- answers to questions
let lastAnswer = "";
function showAnswer(res) {
  const a = res.answer;
  $("#answer-q").textContent = res.transcript ? "« " + res.transcript + " »" : "";
  $("#answer-text").textContent = a.text;
  $("#answer-rows").innerHTML = (a.rows || []).map((r) =>
    `<li><span>${esc(r.label)}</span><b>${esc(r.value)}</b></li>`).join("");
  $("#answer-box").hidden = false;
  lastAnswer = a.speak;
  show("record");
  speak(a.speak);
}
$("#answer-replay").onclick = () => speak(lastAnswer);
document.querySelectorAll(".chips button").forEach((b) => (b.onclick = () => sendVoice(null, b.dataset.q)));

$("#typed-form").onsubmit = (e) => { e.preventDefault(); const v = $("#typed").value.trim(); if (v) sendVoice(null, v); };

$("#photo").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("photo", f);
  const r = await fetch("api/image", { method: "POST", body: fd });
  const res = await r.json();
  if (res.kind === "answer") showAnswer(res); else renderCard(res);
};

// ---------------------------------------------------------------- confirmation card
// Model output and names go into the page: always escape them.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function renderCard(p) {
  current = p;
  $("#ear1").textContent = p.ears.ear1 ?? "—";
  $("#ear2").textContent = p.ears.ear2 ?? "—";
  const v = p.verdict;
  let html = "";

  // --- agent trace: which model, how long, why it asked (Meta-specific, verifiable) ---
  if (p.trace && p.trace.length) {
    const models = [...new Set(p.trace.map(t => t.model).filter(Boolean))];
    html += `<details class="trace"><summary>🔍 Trace · ${p.trace.length} étapes` +
      (models.length ? ` · <span class="models">${models.map(esc).join(" + ")}</span>` : "") +
      ` · ${p.latency_ms}ms</summary>`;
    for (const t of p.trace) {
      html += `<div class="trace-row"><b>${esc(t.step)}</b>`;
      if (t.model) html += ` · <span class="model">${esc(t.model)}</span>`;
      if (t.source) html += ` · <span class="src">${esc(t.source)}</span>`;
      if (t.ms != null) html += ` · ${t.ms}ms`;
      if (t.text) html += `<div class="trace-text">${esc(t.text)}</div>`;
      if (t.changed) html += `<div class="trace-c">↻ lexicon applied</div>`;
      if (t.agree === false) html += `<div class="trace-c bad">⚠ désaccord</div>`;
      if (t.agree === true) html += `<div class="trace-c">✓ accord</div>`;
      if (t.question) html += `<div class="trace-q">→ ${esc(t.question)}</div>`;
      if (t.dropped) html += `<div class="trace-i">⚠ audio droppé (prompt_tokens bas)</div>`;
      if (t.error) html += `<div class="trace-i">⚠ ${esc(t.error)}</div>`;
      if (t.level) html += `<div class="trace-c">level=${esc(t.level)}</div>`;
      if (t.issues && t.issues.length) {
        for (const i of t.issues) html += `<div class="trace-i">⚠ ${esc(i.msg)}</div>`;
      }
      if (t.calls) {
        for (const c of t.calls) {
          const args = JSON.stringify(c.args || {});
          html += `<div class="trace-text">→ <code>${esc(c.tool)}</code>(${esc(args.slice(0, 120))})</div>`;
        }
      }
      html += `</div>`;
    }
    html += `</details>`;
  }

  if (v.level === "clarify") {
    html += `<div class="question">${esc(v.question)}</div>`;
    // Ears disagreed: one tap picks the right reading.
    for (const o of v.options || []) {
      html += `<button class="option" data-choice="${esc(o.id)}">${esc(o.text)}</button>`;
    }
    // Any question: the merchant can also fix the note in words.
    html += `<form class="answer" id="answer-form">
      <input id="answer" value="${esc(p.transcript)}" aria-label="Ahitsio ny fanamarihana">
      <button>Alefa</button></form>`;
  } else {
    for (const l of v.lines) {
      if (l.kind === "sale") html += `<div class="row"><span>${esc(l.qty)} ${esc(l.unit)} ${esc(l.product)}${l.customer ? " · " + esc(l.customer) : ""}</span><span>${fmt(l.total)}</span></div>`;
      if (l.kind === "debt") html += `<div class="row debt"><span>Trosa · ${esc(l.customer)}</span><span>${fmt(l.amount)}</span></div>`;
      if (l.kind === "payment") html += `<div class="row"><span>Naloa · ${esc(l.customer)}</span><span>${fmt(l.amount)}</span></div>`;
      if (l.kind === "stock") html += `<div class="row"><span>Tahiry ${esc(l.product)}</span><span>${l.delta > 0 ? "+" : ""}${esc(l.delta)}</span></div>`;
    }
    if (v.total) html += `<div class="row total"><span>Totaly</span><span>${fmt(v.total)}</span></div>`;
    for (const i of v.issues || []) html += `<div class="issue">⚠ ${esc(i.msg)}</div>`;
    html += previewHtml(v.preview);
    if (v.answer) html += `<div class="card-answer">💬 ${esc(v.answer.text)}</div>`;
  }
  $("#card").innerHTML = html;
  $("#card").querySelectorAll(".option").forEach((b) => (b.onclick = () => answer({ choice: b.dataset.choice })));
  const f = $("#answer-form");
  if (f) f.onsubmit = (e) => { e.preventDefault(); const t = $("#answer").value.trim(); if (t) answer({ text: t }); };
  $("#btn-ok").disabled = v.level === "clarify";
  $("#btn-ok").textContent = v.level === "warn" ? "✓ Marina ihany" : "✓ Marina";
  show("confirm");
  speak(v.speak);
}

// Automatic calculation: what the ledger will look like after ✓ (computed by the server, nothing written yet).
function previewHtml(pv) {
  if (!pv) return "";
  const qty = (x) => Number(x).toLocaleString("fr-FR");
  let h = "";
  for (const c of pv.customers || []) {
    const up = c.after > c.before;
    h += `<div class="pv-row"><span>Trosan'i ${esc(c.name)}</span>` +
      `<span>${fmt(c.before)} → <b class="${up ? "up" : "down"}">${fmt(Math.max(c.after, 0))}</b></span></div>`;
  }
  for (const p of pv.products || []) {
    h += `<div class="pv-row"><span>Tahiry ${esc(p.name)}</span>` +
      `<span>${qty(p.before)} → <b class="${p.after < 10 ? "low" : ""}">${qty(p.after)}</b> ${esc(p.unit)}</span></div>`;
  }
  if (pv.sales_today) {
    h += `<div class="pv-row"><span>Varotra androany</span>` +
      `<span>${fmt(pv.sales_today.before)} → <b>${fmt(pv.sales_today.after)}</b></span></div>`;
  }
  return h ? `<div class="preview"><div class="pv-title">🧮 Kajy ho azy — aorian'ny ✓</div>${h}</div>` : "";
}

// Answer the agent's question → a new plan, which still needs the ✓ tap.
async function answer(body) {
  if (!current) return;
  const r = await fetch("api/clarify", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pending_id: current.pending_id, ...body }) });
  if (!r.ok) { toast("Nisy olana, andramo indray"); return; }
  renderCard(await r.json());
}

$("#btn-ok").onclick = async () => {
  if (!current) return;
  const r = await fetch("api/confirm", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pending_id: current.pending_id }) });
  const res = await r.json();
  current = null;
  $("#answer-box").hidden = true;
  show("record");            // stay ready for the next note
  loadMini();
  toast("Voasoratra ✓", { label: "Foanana", run: () => undo(res.audit_id) });
  speak("Voasoratra.");
  flushQueue();
};

$("#btn-fix").onclick = async () => {
  if (!current) return;
  // Nothing was heard (e.g. mic used in mock mode): ask for the whole note instead.
  if (!(current.transcript || "").trim()) {
    const note = prompt("Tsy nisy re. Soraty ny fanamarihana :");
    if (note && note.trim()) await sendVoice(null, note.trim());
    return;
  }
  // Teach the lexicon, then re-plan: the server applies the new word itself (normalize_ear).
  const heard = prompt("Teny diso (ce qui a été mal compris) :");
  if (!heard) return;
  const canonical = prompt(`Ny marina ho an'ny « ${heard} » :`);
  if (!canonical) return;
  await fetch("api/lexicon", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ heard, canonical, kind: "word" }) });
  await sendVoice(null, current.transcript);
};

async function undo(id) {
  await fetch(`api/undo/${id}`, { method: "POST" });
  toast("Nofoanana");
  loadMini();
  if ($("#screen-dash").classList.contains("active")) loadDash();
}

// ---------------------------------------------------------------- live report on the record screen
async function loadMini() {
  try {
    const d = await (await fetch("api/report")).json();
    $("#m-sales").textContent = fmt(d.sales_total);
    $("#m-debt").textContent = fmt(d.debts.reduce((s, x) => s + x.owed, 0));
    $("#m-debtors").innerHTML = d.debts.slice(0, 3).map((x) =>
      `<li><span>${esc(x.name)}</span><b>${fmt(x.owed)}</b></li>`).join("") ||
      `<li class="none"><span>Tsy misy trosa</span></li>`;
    const low = d.low_stock || [];
    $("#m-low").hidden = !low.length;
    $("#m-low").textContent = low.length ? "⚠ Efa ho lany: " + low.map((x) => `${x.name} ${x.stock} ${x.unit}`).join(", ") : "";
  } catch { /* offline: keep the last numbers */ }
}

// ---------------------------------------------------------------- dashboard + statement
async function loadDash() {
  const d = await (await fetch("api/report")).json();
  $("#k-sales").textContent = fmt(d.sales_total);
  $("#k-debt").textContent = fmt(d.debts.reduce((s, x) => s + x.owed, 0));
  $("#debts").innerHTML = d.debts.map((x) =>
    `<li data-customer="${esc(x.name)}"><span>${esc(x.name)}</span><b>${fmt(x.owed)}</b></li>`
  ).join("") || "<li>Tsy misy</li>";
  $("#low").innerHTML = d.low_stock.map((x) =>
    `<li><span>${esc(x.name)}</span><span>${esc(x.stock)} ${esc(x.unit)}</span></li>`
  ).join("") || "<li>Tsara daholo</li>";
  $("#journal").innerHTML = d.journal.map((j) =>
    `<li><span>#${j.id} ${j.kind === "undo" ? "↩ " : ""}${esc(j.transcript)}</span>` +
    (j.kind === "execute" ? `<button onclick="undo(${j.id})">Foanana</button>` : "") + "</li>").join("");

  // Tap a customer → expand the per-customer debt report
  document.querySelectorAll("#debts li[data-customer]").forEach((li) => {
    li.onclick = async () => {
      const name = li.dataset.customer;
      const r = await (await fetch(`api/debt/${encodeURIComponent(name)}`)).json();
      if (!r.lines.length) return;
      const detail = r.lines.map((l) => {
        const isPay = l.amount < 0;
        const cls = isPay ? "pay" : (l.age_days >= 7 ? "old" : "");
        return `<div class="row"><span>${esc(l.created_at.slice(0, 10))} · ${esc(l.reason || (isPay ? "naloa" : "trosa"))}</span>` +
          `<b>${isPay ? "−" : ""}${fmt(Math.abs(l.amount))}</b>` +
          `<span class="age ${cls}">${isPay ? "" : l.age_days + "j"}</span></div>`;
      }).join("");
      li.insertAdjacentHTML("afterend",
        `<li class="detail"><div class="detail-head">Fitambarany : <b>${fmt(r.total)}</b>` +
        (r.oldest_days > 0 ? ` · tranainy ${r.oldest_days}j` : "") + `</div>${detail}</li>`);
      li.onclick = null;  // don't stack expansions
    };
  });

  // Aged-debt alerts (>= 7 days)
  const a = await (await fetch("api/alerts?days=7")).json();
  if (a.urgent.length) {
    $("#alerts").hidden = false;
    $("#alerts").innerHTML = "<h3>⚠ Trosa efa ela</h3>" + a.urgent.map((x) =>
      `<div class="alert-line">${esc(x.name)} — ${fmt(x.owed)} · ${x.oldest_days} andro</div>`
    ).join("");
  } else {
    $("#alerts").hidden = true;
  }
}

async function loadStatement() {
  const s = await (await fetch("api/statement")).json();
  $("#statement").innerHTML = `
    <h2>Tatitra herinandro</h2>
    <p>${s.from} → ${s.to}</p>
    <div class="row"><span>Varotra (${s.sales_count})</span><b>${fmt(s.sales_total)}</b></div>
    <div class="row"><span>Trosa naloa</span><b>${fmt(s.repaid)}</b></div>
    <div class="seal">${s.chain.ok ? '<span class="ok">✓ Diary tsy nokitihina</span>' : "⚠ Diary simba"} ·
      ${s.chain.rows} andalana<br>Sceau : ${s.chain.head ?? "—"}</div>`;
}

// ---------------------------------------------------------------- PWA
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
netState();
renderQueue();
loadMini();

// ---------------------------------------------------------------- PWA install
let installEvent = null;
addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installEvent = e;
  $("#install").hidden = false;
});
$("#install").onclick = async () => {
  if (!installEvent) return;
  installEvent.prompt();
  const { outcome } = await installEvent.userChoice;
  if (outcome === "accepted") $("#install").hidden = true;
  installEvent = null;
};
addEventListener("appinstalled", () => { $("#install").hidden = true; });