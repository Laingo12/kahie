// Cache the app shell so Kahie opens without network. API calls are never cached.
// Bump this on every deploy, or phones keep the old app.js (cache-first).
const CACHE = "kahie-v18";
const SHELL = [
  "./",
  "index.html",
  "styles.css",
  "app.js",
  "manifest.json",
  "icons/icon.svg",
  "icons/icon-192.png",
  "icons/icon-512.png",
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
        // Cache only real app files: a tunnel's error or warning page (502, ngrok/cloudflare HTML in place of
        // app.js…) must never replace the shell, or the installed app opens blank.
        const html = (res.headers.get("content-type") || "").includes("text/html");
        const isPage = e.request.mode === "navigate";
        if (!res.ok || res.status >= 300) {
          return isPage ? caches.match("index.html").then((r) => r || res) : caches.match(e.request).then((r) => r || res);
        }
        if (isPage === html) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("index.html")))
  );
});