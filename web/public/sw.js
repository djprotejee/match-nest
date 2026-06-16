const CACHE_NAME = "match-nest-v2";
const APP_SHELL = ["/", "/manifest.webmanifest", "/icons/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") {
    return;
  }

  const url = new URL(event.request.url);
  const sameOrigin = url.origin === self.location.origin;
  const isApiLikePath =
    sameOrigin &&
    (url.pathname.startsWith("/health") ||
      url.pathname.startsWith("/auth/") ||
      url.pathname.startsWith("/events") ||
      url.pathname.startsWith("/entities") ||
      url.pathname.startsWith("/timeline") ||
      url.pathname.startsWith("/calendar") ||
      url.pathname.startsWith("/notifications") ||
      url.pathname.startsWith("/settings") ||
      url.pathname.startsWith("/sources") ||
      url.pathname.startsWith("/tournaments") ||
      url.pathname.startsWith("/background") ||
      url.pathname.startsWith("/follows"));

  if (isApiLikePath) {
    event.respondWith(fetch(event.request));
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() =>
        caches.match(event.request).then((cached) => {
          if (cached) {
            return cached;
          }
          if (event.request.mode === "navigate") {
            return caches.match("/");
          }
          throw new Error("Network error");
        }),
      ),
  );
});

self.addEventListener("push", (event) => {
  let payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch {
      payload = { title: "MatchNest", body: event.data.text() };
    }
  }

  const title = payload.title || "MatchNest reminder";
  const options = {
    body: payload.body || "Upcoming event",
    tag: payload.tag || "matchnest-event",
    data: { url: payload.url || "/" },
    icon: "/icons/icon.svg",
    badge: "/icons/icon.svg",
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
