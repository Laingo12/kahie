// Cache the app shell so Kahie opens without network. API calls are never cached.
// Bump this on every deploy, or phones keep the old app.js (cache-first).
const CACHE = "kahie-v3";
const SHELL = ["./", "index.html", "styles.css", "app.js", "manifest.json", "icons/icon.svg"];

self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL))));
self.addEventListener("activate", (e) =>
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))));
self.addEventListener("fetch", (e) => {
  if (new URL(e.request.url).pathname.includes("/api/")) return;
  e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
});
