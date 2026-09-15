const CACHE = "aether-shell-v5";
const SHELL = ["/", "/assets/app.css?v=11", "/assets/js/markdown.js?v=11", "/assets/js/api.js?v=11", "/assets/js/speech.js?v=11", "/assets/js/deck.js?v=11", "/assets/js/app.js?v=11"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return;
  if (url.pathname.startsWith("/api/")) return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request).then((hit) => hit || caches.match("/")))
  );
});
