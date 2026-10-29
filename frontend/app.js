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
  if (!voiceOn) stopSpeaking();
  renderVoice();
};
function pickVoice() {
  const vs = ("speechSynthesis" in window) ? speechSynthesis.getVoices() : [];
  return vs.find((v) => /^mg/i.test(v.lang)) || vs.find((v) => /^fr/i.test(v.lang)) || null;
}
// Phonetic hack: browsers ship no Malagasy voice, so a French voice reads Malagasy. Respell the text
// so French pronounces it the Malagasy way (only for the speech; the screen keeps the real spelling).
//   o → ou (vary « arivo » → arivou)   ao → aou   final y → i   j → dz   e → é (Malagasy e is always [e])
//   s between vowels → ss (misy → missi, not « mizi »)   an/en/in/on + consonant → ann… (no French nasal)
//   g before e/i → gu (hard g)
function to_phonetic_mg(text) {
  return String(text || "").replace(/[A-Za-zÀ-ÿ']+/g, (w) => {
    if (/[éèêàùç]/i.test(w)) return w;                     // already French spelling (crédit, payé…): leave it
    const cap = w[0] === w[0].toUpperCase();
    let x = w.toLowerCase()
      .replace(/ao/g, "aou")
      .replace(/o(?!u)/g, "ou")
      .replace(/y$/g, "i")
      .replace(/j/g, "dz")
      .replace(/([aeiou])s(?=[aeiouy])/g, "$1ss")
      .replace(/([aeio])n(?=[bcdfghjklmpqrstvwxz])/g, "$1nn")
      .replace(/g(?=[ei])/g, "gu")
      .replace(/e/g, "é");
    return cap ? x[0].toUpperCase() + x.slice(1) : x;
  });
}

// onEnd runs only when the speech really finished: the mic never opens while Kahie is talking
// (otherwise it would hear its own "… Marina ve?"). `speechDone` is the same moment as a promise,
// awaited by the hands-free loop.
// Server voice (/api/speak, Gemini TTS, on only when the server has GEMINI_API_KEY) is tried first;
// any failure falls back to the phone's voice.
let serverVoice = false, playing = null, lastSpoken = "";
let speechDone = Promise.resolve(), endSpeech = () => {};
fetch("api/health").then((r) => r.json()).then((h) => { serverVoice = h.tts === "server"; }).catch(() => {});

function stopSpeaking() {
  if (playing) { try { playing.pause(); } catch {} playing = null; }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  endSpeech();
}

function speak(text, onEnd) {
  if (!voiceOn || !text) return false;
  stopSpeaking();
  lastSpoken = text;
  let ended = false, resolve;
  speechDone = new Promise((r) => (resolve = r));
  // Safety cap: some phones never fire "end"; the loop must not wait forever.
  const cap = setTimeout(() => finish(false), 4000 + text.length * 150);
  function finish(callOnEnd = true) {
    if (ended) return;
    ended = true; clearTimeout(cap); playing = null;
    setTimeout(() => { resolve(); if (callOnEnd && onEnd) onEnd(); }, 300);
  }
  endSpeech = () => finish(false);
  if (serverVoice) {
    let fellBack = false;
    const fallback = () => { if (!fellBack && !ended) { fellBack = true; playing = null; if (!phoneSpeak(text, finish)) finish(); } };
    fetch("api/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => {
        if (ended) return;
        const a = new Audio(`data:${d.mime || "audio/wav"};base64,${d.audio}`);
        playing = a;
        a.onended = () => finish();
        a.onerror = fallback;
        return a.play();
      })
      .catch(fallback);
    return true;
  }
  if (!phoneSpeak(text, finish)) { finish(false); return false; }
  return true;
}

function phoneSpeak(text, done) {
  if (!("speechSynthesis" in window)) return false;
  speechSynthesis.cancel();
  const v = pickVoice();
  const malagasyVoice = v && /^mg/i.test(v.lang);
  const u = new SpeechSynthesisUtterance(malagasyVoice ? text : to_phonetic_mg(text));
  if (v) { u.voice = v; u.lang = v.lang; } else { u.lang = "fr-FR"; }
  u.rate = 0.95;
  u.onend = () => done();
  u.onerror = () => done();
  speechSynthesis.speak(u);
  return true;
}

// Resolves once Kahie is silent (also if a new sentence started meanwhile).
async function quiet() {
  for (;;) { const p = speechDone; await p; if (p === speechDone) return; }
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
  $("#queue").textContent = `${q.length} feo miandry ny tambajotra`;
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

// ---------------------------------------------------------------- hands-free conversation
// One tap on the mic opens the conversation (browsers require that one gesture for the microphone
// and for sound). From then on everything is by voice: notes, questions, "eny / tsia", commands
// ("asehoy ny tabilao", "foano ilay farany", "avereno", "ajanony"). Kahie never listens while it
// speaks, and nothing is written without a clear "eny" (or the ✓ tap, which always stays).
let handsFree = false, micStream = null, audioCtx = null, analyser = null;
let pendingUndo = null, undoAttempt = 0;

function setState(text) {
  $("#rec-state").textContent = text;
  $("#listen-text").textContent = text;
  $("#listen-bar").hidden = !(handsFree && current);
  $("#mic").classList.toggle("rec", handsFree);
}

async function openMic() {
  if (micStream) return;
  micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  analyser = audioCtx.createAnalyser(); analyser.fftSize = 2048;
  audioCtx.createMediaStreamSource(micStream).connect(analyser);
}
function closeMic() {
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  if (audioCtx) audioCtx.close().catch(() => {});
  micStream = audioCtx = analyser = null;
}

async function startConversation() {
  if (handsFree) return;
  try { await openMic(); } catch { setState("Tsy azo ampiasaina ny mikrô"); return; }
  handsFree = true;
  try { localStorage.setItem("kahie-handsfree", "on"); } catch {}
  $("#wake").hidden = true;
  setState("Henoiko… mitenena");
  speak("Manao ahoana! Henoiko ianao.");
  conversation();
}

function stopConversation(remember = true) {
  handsFree = false;
  if (listening) listening.cancel();
  closeMic();
  if (remember) { try { localStorage.setItem("kahie-handsfree", "off"); } catch {} }
  setState("Vonona — tsindrio ny mikrô");
}

$("#mic").onclick = () => (handsFree ? stopConversation() : startConversation());

async function conversation() {
  while (handsFree) {
    await quiet();                                   // never listen while Kahie talks
    if (!handsFree) break;
    setState(pendingUndo ? "Eny sa tsia?" : current ? (current.verdict.level === "clarify" ? "Henoiko ny valinao…" : "Henoiko… (eny / tsia)") : "Henoiko… mitenena");
    let blob = null;
    try { blob = await listenOnce({ waitMs: Infinity, rotateMs: 3000, maxMs: 20000 }); }
    catch { stopConversation(false); setState("Tsy azo ampiasaina ny mikrô"); break; }
    if (!blob || !handsFree) continue;
    setState("Mandinika…");
    try { await route(blob); }
    catch { toast("Nisy olana, andramo indray"); }
  }
}

async function route(blob) {
  if (!navigator.onLine) { await enqueue(blob); speak("Voatahiry. Halefa rehefa misy tambajotra."); return; }
  if (pendingUndo) return undoReply(blob);
  if (current) return sendReply(blob);
  lastWasVoice = true;
  return sendVoice(blob);
}

async function undoReply(blob) {
  const fd = new FormData();
  fd.append("audit_id", String(pendingUndo.audit_id));
  fd.append("audio", blob, "reply.webm");
  fd.append("attempt", String(undoAttempt));
  const r = await fetch("api/undo_reply", { method: "POST", body: fd });
  if (!r.ok) { pendingUndo = null; toast("Tsy afaka nofoanana"); return; }
  const res = await r.json();
  if (res.action === "repeat" && undoAttempt < 1) { undoAttempt += 1; speak(res.speak); return; }
  pendingUndo = null;
  toast(res.action === "repeat" ? "Tsy nofoanana" : res.message);
  speak(res.action === "repeat" ? "Tsy nofoanana." : res.speak);
  loadMini();
  if ($("#screen-dash").classList.contains("active")) loadDash();
}

// Spoken command answered by the server (before any planning).
function runCommand(res) {
  const c = res.command;
  if (c === "repeat") { speak(lastSpoken || "Henoiko ianao."); return; }
  if (c === "dash") show("dash");
  if (c === "statement") show("statement");
  if (c === "home") show("record");
  if (c === "undo" && res.undo) { pendingUndo = res.undo; undoAttempt = 0; }
  if (res.message) toast(res.message);
  speak(res.speak);
  if (c === "stop") stopConversation();
}

// The app was in conversation mode last time: one tap anywhere starts it again.
try {
  if (localStorage.getItem("kahie-handsfree") === "on" && navigator.mediaDevices) {
    $("#wake").hidden = false;
    const wake = () => { removeEventListener("pointerdown", wake, true); startConversation(); };
    addEventListener("pointerdown", wake, true);
  }
} catch {}

async function sendVoice(blob, text) {
  setState("Mandinika…");
  const fd = new FormData();
  if (blob) fd.append("audio", blob, "note.webm");
  if (text) fd.append("text", text);
  const r = await fetch("api/voice", { method: "POST", body: fd });
  if (!r.ok) { setState("Nisy olana, andramo indray"); throw new Error(await r.text()); }
  const res = await r.json();
  if (res.kind === "command") runCommand(res);
  else if (res.kind === "answer") showAnswer(res);
  else renderCard(res);
  if (!handsFree) setState("Vonona — tsindrio ny mikrô");
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
document.querySelectorAll(".chips button").forEach((b) => (b.onclick = () => { lastWasVoice = false; sendVoice(null, b.dataset.q); }));

$("#typed-form").onsubmit = (e) => { e.preventDefault(); const v = $("#typed").value.trim(); if (v) { lastWasVoice = false; sendVoice(null, v); } };

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
// Quantities: 0.5 kg → "500 g", 2.5 → "2,5" (decimal comma).
function qtyFmt(q, unit) {
  const n = Number(q);
  if (unit === "kg" && n > 0 && n < 1) return `${Math.round(n * 1000)} g`;
  return `${n.toLocaleString("fr-FR", { maximumFractionDigits: 3 })} ${esc(unit || "")}`;
}

// Model output and names go into the page: always escape them.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function renderCard(p) {
  current = p;
  // An ear whose reading was rejected (silence, reply about the audio, implausible) shows WHY, never the rejected text.
  for (const ear of ["ear1", "ear2"]) {
    const heard = (p.ears && p.ears[ear]) || "";
    const t = (p.trace || []).find((x) => x.step === ear);
    $("#" + ear).textContent = heard || (t && t.error ? "— " + t.error : "—");
    $("#" + ear).classList.toggle("ear-empty", !heard);
  }
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
      if (t.rejected) html += `<div class="trace-i">✗ nolavina: « ${esc(t.rejected.slice(0, 120))} »</div>`;
      if (t.missing && t.missing.length) html += `<div class="trace-i">✗ tsy re tao amin'ny feo: ${esc(t.missing.join(", "))}</div>`;
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
    // The yes/no question Kahie reads aloud, also written for those who read rather than listen.
    if (v.spoken_confirmation) html += `<div class="card-ask">${esc(v.spoken_confirmation)}</div>`;
    for (const l of v.lines) {
      if (l.kind === "sale") html += `<div class="row"><span>${qtyFmt(l.qty, l.unit)} ${esc(l.product)}${l.customer ? " · " + esc(l.customer) : ""}</span><span>${fmt(l.total)}</span></div>`;
      if (l.kind === "debt") html += `<div class="row debt"><span>Trosa · ${esc(l.customer)}</span><span>${fmt(l.amount)}</span></div>`;
      if (l.kind === "payment") html += `<div class="row"><span>Naloa · ${esc(l.customer)}</span><span>${fmt(l.amount)}</span></div>`;
      if (l.kind === "stock") html += `<div class="row"><span>${l.delta > 0 ? "Niditra" : "Simba / very"} · ${esc(l.product)}</span>` +
        `<span>${l.delta > 0 ? "+" : "−"}${qtyFmt(Math.abs(l.delta_spoken ?? l.delta), l.unit || l.base_unit)}</span></div>`;
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
  replyAttempt = 0;
  // Two-way voice: if the note was spoken, Kahie reads the card / asks its question, then listens.
  speak(v.speak, lastWasVoice && !handsFree ? () => listenReply() : null);
}

// ---------------------------------------------------------------- listening
let lastWasVoice = false, replyAttempt = 0, listening = null;

// Record ONE utterance from the open mic. Speech = level above the room's noise for 0.3 s;
// stops 1.2 s after the speaker goes quiet (or at maxMs). While nobody speaks, the recorder is
// restarted every `rotateMs` so long silences are never sent. Returns null if nobody spoke
// (nothing is sent, nothing is guessed).
async function listenOnce({ maxMs = 8000, silenceMs = 1200, waitMs = 5000, rotateMs = 0 } = {}) {
  await openMic();
  const buf = new Float32Array(analyser.fftSize);
  return new Promise((resolve) => {
    let rec = null, parts = [], spoke = false, voiced = 0, quietSince = null, floor = 0.01;
    const t0 = performance.now(); let segStart = t0, speechStart = null;
    const startRec = () => {
      parts = [];
      rec = new MediaRecorder(micStream);
      rec.ondataavailable = (e) => { if (e.data.size) parts.push(e.data); };
      rec.start(); segStart = performance.now();
    };
    const finish = (keep) => {
      clearInterval(timer); listening = null;
      const r = rec, mine = parts;
      r.onstop = () => {
        if (!handsFree) closeMic();
        resolve(keep && spoke ? new Blob(mine, { type: r.mimeType }) : null);
      };
      if (r.state !== "inactive") r.stop(); else r.onstop();
    };
    startRec();
    const timer = setInterval(() => {
      analyser.getFloatTimeDomainData(buf);
      const rms = Math.sqrt(buf.reduce((s, x) => s + x * x, 0) / buf.length);
      const thr = Math.max(0.015, floor * 3);
      const now = performance.now();
      if (!spoke) {
        if (rms > thr) { voiced += 1; speechStart = speechStart || now; if (voiced >= 3) spoke = true; }
        else { voiced = 0; speechStart = null; floor = 0.9 * floor + 0.1 * rms; }
        if (now - t0 > waitMs) return finish(false);
        // Nothing said yet: drop the silent recording and start a fresh one.
        if (rotateMs && !speechStart && now - segStart > rotateMs) {
          const old = rec; old.ondataavailable = null; old.onstop = null;
          if (old.state !== "inactive") old.stop();
          startRec();
        }
      } else {
        if (rms > thr) quietSince = null;
        else { quietSince = quietSince || now; if (now - quietSince > silenceMs) return finish(true); }
        if (now - (speechStart || t0) > maxMs) return finish(true);
      }
    }, 100);
    // stop = "I finished talking" (send what was said); cancel = a button was tapped instead.
    listening = { stop: () => finish(true), cancel: () => finish(false) };
  });
}

// Tap mode (conversation off): after the card is read, listen once for the answer.
async function listenReply() {
  if (!current || listening || handsFree) return;
  $("#listen-bar").hidden = false;
  $("#btn-voice-reply").hidden = true;
  $("#listen-text").textContent = current.verdict.level === "clarify" ? "Henoiko ny valinao…" : "Henoiko… (eny / tsia)";
  let blob = null;
  try { blob = await listenOnce(); } catch { $("#listen-bar").hidden = true; $("#btn-voice-reply").hidden = false; return; }   // no mic permission
  $("#listen-bar").hidden = true;
  $("#btn-voice-reply").hidden = false;
  if (!blob || !current) return;              // nobody spoke: the card stays, the ✓ button still works
  await sendReply(blob);
}

async function sendReply(blob) {
  const fd = new FormData();
  fd.append("pending_id", current.pending_id);
  fd.append("audio", blob, "reply.webm");
  fd.append("attempt", String(replyAttempt));
  const r = await fetch("api/reply", { method: "POST", body: fd });
  if (!r.ok) { toast("Nisy olana, andramo indray"); return; }
  handleReply(await r.json());
}

function handleReply(res) {
  if (res.action === "confirmed") {
    afterWrite(res);
  } else if (res.action === "cancelled") {
    current = null; show("record"); toast(res.message); speak(res.speak);
  } else if (res.action === "repeat") {
    replyAttempt += 1;
    toast(res.message);
    // Ask again (twice at most); after that Kahie waits quietly — the buttons are there.
    if (replyAttempt <= 2) speak(res.speak, !handsFree && replyAttempt <= 1 ? () => listenReply() : null);
  } else if (res.action === "plan") {
    const p = res.result;
    if (p.kind === "answer") showAnswer(p); else renderCard(p);
  }
}

$("#listen-stop").onclick = () => (handsFree ? stopConversation() : listening && listening.stop());
$("#btn-voice-reply").onclick = () => { stopSpeaking(); listenReply(); };

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
    h += `<div class="pv-row"><span>${esc(p.name.charAt(0).toUpperCase() + p.name.slice(1))} sisa</span>` +
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
  if (listening) listening.cancel();
  const r = await fetch("api/clarify", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pending_id: current.pending_id, ...body }) });
  if (!r.ok) { toast("Nisy olana, andramo indray"); return; }
  renderCard(await r.json());
}

$("#btn-ok").onclick = async () => {
  if (!current) return;
  if (listening) listening.cancel();
  const r = await fetch("api/confirm", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pending_id: current.pending_id }) });
  afterWrite(await r.json());
};

function afterWrite(res) {
  current = null;
  $("#answer-box").hidden = true;
  show("record");            // stay ready for the next note
  loadMini();
  toast(res.message || "Voasoratra ✓", { label: "Foanana", run: () => undo(res.audit_id) });
  speak(res.speak || "Voasoratra.");
  flushQueue();
};

$("#btn-fix").onclick = async () => {
  if (!current) return;
  if (listening) listening.cancel();
  // Nothing was heard (e.g. mic used in mock mode): ask for the whole note instead.
  if (!(current.transcript || "").trim()) {
    const note = prompt("Tsy nisy re. Soraty eto ilay izy:");
    if (note && note.trim()) await sendVoice(null, note.trim());
    return;
  }
  // Teach the lexicon, then re-plan: the server applies the new word itself (normalize_ear).
  const heard = prompt("Inona ilay teny diso henony?");
  if (!heard) return;
  const canonical = prompt(`Inona no tena izy fa tsy « ${heard} »?`);
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
        return `<div class="row"><span>${esc(l.created_at.slice(0, 10))} · ${isPay ? "naloa" : "trosa"}</span>` +
          `<b>${isPay ? "−" : ""}${fmt(Math.abs(l.amount))}</b>` +
          `<span class="age ${cls}">${isPay ? "" : l.age_days + " andro"}</span></div>`;
      }).join("");
      li.insertAdjacentHTML("afterend",
        `<li class="detail"><div class="detail-head">Fitambarany: <b>${fmt(r.total)}</b>` +
        (r.oldest_days > 0 ? ` · efa ${r.oldest_days} andro` : "") + `</div>${detail}</li>`);
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