const CACHE_PREFIX = "bass-shell-";
const CACHE_NAME = `${CACHE_PREFIX}v1`;
const ROOT_URL = new URL("/", self.location.origin).href;
const FIXED_SHELL_PATHS = [
  "/manifest.webmanifest",
  "/icons/bass-icon.svg",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/icon-maskable-512.png",
  "/icons/apple-touch-icon-180.png",
];
const STATIC_DESTINATIONS = new Set(["font", "image", "manifest", "script", "style"]);

function isDeviceApiRequest(url) {
  return (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/sim/") ||
    url.pathname === "/health" ||
    url.pathname === "/ready"
  );
}

function isStaticRequest(request, url) {
  return (
    STATIC_DESTINATIONS.has(request.destination) ||
    url.pathname.startsWith("/assets/") ||
    url.pathname.startsWith("/icons/") ||
    url.pathname === "/manifest.webmanifest"
  );
}

async function cacheAppShell() {
  const cache = await caches.open(CACHE_NAME);
  const rootResponse = await fetch(new Request(ROOT_URL, { cache: "reload" }));
  if (!rootResponse.ok) throw new Error(`Could not cache app shell (${rootResponse.status})`);

  const html = await rootResponse.clone().text();
  const discovered = [...html.matchAll(/\b(?:src|href)=["']([^"']+)["']/g)]
    .map((match) => new URL(match[1], self.location.origin))
    .filter((url) => url.origin === self.location.origin)
    .map((url) => url.href);
  const shellUrls = new Set([
    ...FIXED_SHELL_PATHS.map((path) => new URL(path, self.location.origin).href),
    ...discovered,
  ]);

  await cache.put(ROOT_URL, rootResponse);
  await Promise.all(
    [...shellUrls].map(async (url) => {
      if (url === ROOT_URL) return;
      const response = await fetch(new Request(url, { cache: "reload" }));
      if (!response.ok) throw new Error(`Could not cache ${url} (${response.status})`);
      await cache.put(url, response);
    }),
  );
}

async function networkFirstNavigation(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(ROOT_URL, response.clone());
    return response;
  } catch {
    const cached = await cache.match(ROOT_URL);
    if (cached) return cached;
    throw new Error("BASS is offline and the app shell is not cached yet");
  }
}

async function staleWhileRevalidate(request, event) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  const network = fetch(request).then(async (response) => {
    if (response.ok && response.type === "basic") await cache.put(request, response.clone());
    return response;
  });

  if (cached) {
    event.waitUntil(network.catch(() => undefined));
    return cached;
  }
  return network;
}

self.addEventListener("install", (event) => {
  event.waitUntil(cacheAppShell().then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
            .map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Device/face state and commands must always reach the network. Cross-origin
  // requests (the normal Pi/Face API setup) are intentionally untouched too.
  if (request.method !== "GET" || url.origin !== self.location.origin || isDeviceApiRequest(url)) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(networkFirstNavigation(request));
    return;
  }

  if (isStaticRequest(request, url)) {
    event.respondWith(staleWhileRevalidate(request, event));
  }
});
