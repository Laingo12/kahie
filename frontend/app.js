const $ = (s) => document.querySelector(s);
const fmt = (n) => new Intl.NumberFormat("fr-FR").format(Math.round(n)) + " Ar";
let current = null;
let isListening = false;
let isSpeaking = false;
let recognition = null;

// ---------------------------------------------------------------- DEBUG
function logDebug(msg, isError = false) {
  console.log(msg);
  const dbg = document.getElementById("debug-box");
  if (dbg) {
    dbg.style.display = "block";
    const p = document.createElement("div");
    p.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    if (isError) p.style.color = "red";
    dbg.appendChild(p);
    dbg.scrollTop = dbg.scrollHeight;
  }
}

// ---------------------------------------------------------------- SPEECH RECOGNITION
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

if (SpeechRecognition) {
  recognition = new SpeechRecognition();
  recognition.lang = "fr-FR";
  recognition.continuous = false; // Aleo false, fa isika no mamerina
  recognition.interimResults = true;

  recognition.onstart = () => {
    logDebug("Mic: Nanomboka.");
    isListening = true;
    $("#mic").classList.add("rec");
    $("#rec-state").textContent = "Mihaino... (mitenena)";
  };

  recognition.onresult = (event) => {
    let final = "";
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; ++i) {
      if (event.results[i].isFinal) final += event.results[i][0].transcript;
      else interim += event.results[i][0].transcript;
    }
    if (interim) $("#rec-state").textContent = interim;

    if (final) {
      const t = final.toLowerCase();
      logDebug(`Feo re: "${final}"`);

      if (current && (current.verdict.level === "ok" || current.verdict.level === "warn")) {
        if (t.includes("eny") || t.includes("marina") || t.includes("eo") || t.includes("oui")) {
          logDebug("Confirmation (Eny). Manao confirm...");
          $("#btn-ok").click();
          return;
        }
      }
      $("#rec-state").textContent = "Mandinika…";
      sendVoice(null, final);
    }
  };

  recognition.onerror = (e) => {
    logDebug(`Mic Error: ${e.error}`, true);
    if (e.error === 'not-allowed') $("#rec-state").textContent = "Tsy nahazo alalana ny Mic.";
  };

  recognition.onend = () => {
    logDebug("Mic: Nijanona.");
    if (isListening && !isSpeaking) {
      setTimeout(() => { if (isListening && !isSpeaking) startListening(); }, 300);
    } else {
      $("#mic").classList.remove("rec");
      if (!isSpeaking) $("#rec-state").textContent = "Vonona";
    }
  };
} else {
  logDebug("Tsy manohana SpeechRecognition ity navigateur ity.", true);
}

function startListening() {
  if (!recognition || isSpeaking || isListening) return;
  try { recognition.start(); } catch (e) { logDebug("Efa mandeha.", true); }
}

function stopListening() {
  if (!recognition) return;
  isListening = false;
  try { recognition.stop(); } catch(e){}
  $("#mic").classList.remove("rec");
  $("#rec-state").textContent = "Vonona";
}

$("#mic").onclick = () => {
  if (isListening) stopListening();
  else { isListening = true; startListening(); }
};

// ---------------------------------------------------------------- TEXT TO SPEECH (Gemini)
async function speak(text) {
  if (!text) return;
  isSpeaking = true;
  if (isListening && recognition) { try { recognition.stop(); } catch(e){} }

  try {
    const r = await fetch("api/speak", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text })
    });
    if (!r.ok) throw new Error("TTS failed");
    const data = await r.json();

    if (data.audio) {
      const audio = new Audio("data:audio/wav;base64," + data.audio);
      $("#rec-state").textContent = "Kahie miteny…";
      audio.onended = () => { isSpeaking = false; if (isListening) startListening(); };
      audio.play().catch(e => {
        logDebug("Autoplay no-block. Tsindrio ny écran.", true);
        isSpeaking = false; if (isListening) startListening();
      });
    } else {
      isSpeaking = false; if (isListening) startListening();
    }
  } catch (e) {
    logDebug("TTS Error: " + e.message, true);
    isSpeaking = false; if (isListening) startListening();
  }
}

// ---------------------------------------------------------------- NAVIGATION
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

// ---------------------------------------------------------------- API CALLS
async function sendVoice(blob, text) {
  $("#rec-state").textContent = "Mandinika…";
  const fd = new FormData();
  if (blob) fd.append("audio", blob, "note.webm");
  if (text) fd.append("text", text);
  try {
    const r = await fetch("api/voice", { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    renderCard(await r.json());
  } catch (e) {
    logDebug("Backend Error: " + e.message, true);
    $("#rec-state").textContent = "Nisy olana.";
  }
}

$("#typed-form").onsubmit = (e) => { e.preventDefault(); const v = $("#typed").value.trim(); if (v) sendVoice(null, v); };

$("#photo").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("photo", f);
  const r = await fetch("api/image", { method: "POST", body: fd });
  renderCard(await r.json());
};

// ---------------------------------------------------------------- RENDER CARD
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function renderCard(p) {
  current = p;
  $("#ear1").textContent = p.ears.ear1 ?? "—";
  $("#ear2").textContent = p.ears.ear2 ?? "—";
  const v = p.verdict;
  let html = "";

  if (p.trace && p.trace.length) {
    const models = [...new Set(p.trace.map(t => t.model).filter(Boolean))];
    html += `<details class="trace"><summary>🔍 Trace · ${p.trace.length} étapes` + (models.length ? ` · <span class="models">${models.map(esc).join(" + ")}</span>` : "") + `</summary>`;
    for (const t of p.trace) {
      html += `<div class="trace-row"><b>${esc(t.step)}</b>`;
      if (t.model) html += ` · <span class="model">${esc(t.model)}</span>`;
      if (t.source) html += ` · <span class="src">${esc(t.source)}</span>`;
      if (t.text) html += `<div class="trace-text">${esc(t.text)}</div>`;
      if (t.question) html += `<div class="trace-q">→ ${esc(t.question)}</div>`;
      if (t.error) html += `<div class="trace-i">⚠ ${esc(t.error)}</div>`;
      if (t.calls) { for (const c of t.calls) html += `<div class="trace-text">→ <code>${esc(c.tool)}</code>(${esc(JSON.stringify(c.args).slice(0,50))})</div>`; }
      html += `</div>`;
    }
    html += `</details>`;
  }

  if (v.level === "clarify") {
    speak(v.question);
    html += `<div class="question">${esc(v.question)}</div>`;
    for (const o of v.options || []) html += `<button class="option" data-choice="${esc(o.id)}">${esc(o.text)}</button>`;
    html += `<form class="answer" id="answer-form"><input id="answer" value="${esc(p.transcript)}"><button>Alefa</button></form>`;
  } else {
    if (p.spoken_confirmation) speak(p.spoken_confirmation);
    for (const l of v.lines) {
      if (l.kind === "sale") html += `<div class="row"><span>${esc(l.qty)} ${esc(l.unit)} ${esc(l.product)}${l.customer ? " · " + esc(l.customer) : ""}</span><span>${fmt(l.total)}</span></div>`;
      if (l.kind === "debt") html += `<div class="row debt"><span>Trosa · ${esc(l.customer)}</span><span>${fmt(l.amount)}</span></div>`;
      if (l.kind === "payment") html += `<div class="row"><span>Naloa · ${esc(l.customer)}</span><span>${fmt(l.amount)}</span></div>`;
      if (l.kind === "stock") html += `<div class="row"><span>Tahiry ${esc(l.product)}</span><span>${l.delta > 0 ? "+" : ""}${esc(l.delta)}</span></div>`;
    }
    if (v.total) html += `<div class="row total"><span>Totaly</span><span>${fmt(v.total)}</span></div>`;
    for (const i of v.issues || []) html += `<div class="issue">⚠ ${esc(i.msg)}</div>`;
  }

  $("#card").innerHTML = html;
  $("#card").querySelectorAll(".option").forEach((b) => (b.onclick = () => answer({ choice: b.dataset.choice })));
  const f = $("#answer-form");
  if (f) f.onsubmit = (e) => { e.preventDefault(); const t = $("#answer").value.trim(); if (t) answer({ text: t }); };

  $("#btn-ok").disabled = v.level === "clarify";
  show("confirm");
}

async function answer(body) {
  if (!current) return;
  const r = await fetch("api/clarify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pending_id: current.pending_id, ...body }) });
  if (r.ok) renderCard(await r.json());
}

$("#btn-ok").onclick = async () => {
  if (!current) return;
  const r = await fetch("api/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pending_id: current.pending_id }) });
  const res = await r.json();
  current = null;
  show("dash");
  toast("Voasoratra ✓", { label: "Foanana", run: () => undo(res.audit_id) });

  // Mamaky ny bilan
  const report = await (await fetch("api/report")).json();
  const balance = report.debts.reduce((s, x) => s + x.owed, 0);
  speak(`Voasoratra. Ny bilan: manana trosa ${balance} Ar.`);
};

$("#btn-fix").onclick = () => {
  if (!current) return;
  const heard = prompt("Teny diso:"); if (!heard) return;
  const canonical = prompt(`Ny marina ho an'ny « ${heard} » :`); if (!canonical) return;
  fetch("api/lexicon", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ heard, canonical, kind: "word" }) })
    .then(() => sendVoice(null, current.transcript));
};

async function undo(id) { await fetch(`api/undo/${id}`, { method: "POST" }); toast("Nofoanana"); loadDash(); }

async function loadDash() {
  const d = await (await fetch("api/report")).json();
  $("#k-sales").textContent = fmt(d.sales_total);
  $("#k-debt").textContent = fmt(d.debts.reduce((s, x) => s + x.owed, 0));
  $("#debts").innerHTML = d.debts.map((x) => `<li data-customer="${esc(x.name)}"><span>${esc(x.name)}</span><b>${fmt(x.owed)}</b></li>`).join("") || "<li>Tsy misy</li>";
}

async function loadStatement() {
  const s = await (await fetch("api/statement")).json();
  $("#statement").innerHTML = `<h2>Tatitra</h2><div class="row"><span>Varotra</span><b>${fmt(s.sales_total)}</b></div><div class="seal">${s.chain.ok ? '✓ Marina' : "⚠ Simba"}</div>`;
}

// ---------------------------------------------------------------- PWA
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
let installEvent = null;
addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvent = e; $("#install").hidden = false; });
$("#install").onclick = async () => { if (!installEvent) return; installEvent.prompt(); const { outcome } = await installEvent.userChoice; if (outcome === "accepted") $("#install").hidden = true; };