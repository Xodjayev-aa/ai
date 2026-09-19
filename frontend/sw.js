/* Aether service worker — cache the static shell, never cache the API.
 *
 * Rules:
 *  - /api/* is network-only (streaming, auth, uploads must never be cached).
 *  - Navigations are network-first with an offline fallback to the shell.
 *  - Other static assets are cache-first with a background refresh.
 *  - Pre-caching uses allSettled: a single missing file must never break the
 *    install (a failed `addAll` leaves the app with no service worker at all).
 */
const CACHE = "aether-v11";
const SHELL = [
  "/", "/index.html", "/assets/app.css", "/manifest.json",
  "/icons/favicon.svg", "/icons/favicon-16.png", "/icons/favicon-32.png",
  "/icons/apple-touch-icon.png", "/icons/icon-192.png", "/icons/icon-512.png",
  "/assets/js/core.js", "/assets/js/chat.js", "/assets/js/voice.js",
  "/assets/js/slides.js", "/assets/js/images.js", "/assets/js/settings.js",
  "/assets/js/tools.js", "/assets/js/admin.js", "/assets/js/app.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return;               // network only

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match("/index.html").then((hit) => hit || Response.error()))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((hit) => {
      const network = fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      }).catch(() => hit || Response.error());
      return hit || network;
    })
  );
});
