// Cache the app shell so Kahie opens without network. API calls are never cached.
// Bump this on every deploy, or phones keep the old app.js (cache-first).
const CACHE = "kahie-v14";
const SHELL = [
  "./",
  "index.html",
  "styles.css",
  "app.js",
  "manifest.json",
  "icons/icon.svg",
  "img/paper.jpg",
  "img/logo-k.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((ks) =>
      Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  // Never cache API calls — they must always hit the network.
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/") || url.pathname.includes("/api/")) return;

  // Only handle GET requests.
  if (e.request.method !== "GET") return;

  // Network-first for the shell, so a new deploy lands fast; fallback to cache offline.
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("index.html")))
  );
});