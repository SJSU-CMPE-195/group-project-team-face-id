import { readFile } from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const distDir = path.join(repoRoot, "dist");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function distPathFromUrl(url) {
  const pathname = new URL(url, "https://bass.test").pathname;
  return path.join(distDir, pathname === "/" ? "index.html" : pathname.slice(1));
}

function pngSize(buffer) {
  const signature = "89504e470d0a1a0a";
  assert(buffer.subarray(0, 8).toString("hex") === signature, "Icon is not a PNG");
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

const html = await readFile(path.join(distDir, "index.html"), "utf8");
assert(/<link[^>]+rel=["']manifest["'][^>]+href=["']\/manifest\.webmanifest["']/.test(html), "index.html is missing the web manifest link");

const manifest = JSON.parse(await readFile(path.join(distDir, "manifest.webmanifest"), "utf8"));
assert(manifest.name && manifest.short_name, "Manifest needs name and short_name");
assert(manifest.start_url === "/" && manifest.scope === "/", "Manifest start_url/scope must be root");
assert(manifest.display === "standalone", "Manifest display must be standalone");
assert(manifest.prefer_related_applications === false, "Manifest must not prefer another app");

const requiredSizes = new Set(["192x192", "512x512"]);
for (const icon of manifest.icons ?? []) {
  const data = await readFile(distPathFromUrl(icon.src));
  const size = pngSize(data);
  assert(`${size.width}x${size.height}` === icon.sizes, `${icon.src} dimensions do not match ${icon.sizes}`);
  requiredSizes.delete(icon.sizes);
}
assert(requiredSizes.size === 0, `Manifest is missing icon size(s): ${[...requiredSizes].join(", ")}`);
assert((manifest.icons ?? []).some((icon) => icon.purpose?.split(/\s+/).includes("maskable")), "Manifest needs a maskable icon");

const assetUrls = [...html.matchAll(/\b(?:src|href)=["'](\/assets\/[^"']+)["']/g)].map((match) => match[1]);
assert(assetUrls.some((url) => url.endsWith(".js")), "Built HTML has no JavaScript asset");
assert(assetUrls.some((url) => url.endsWith(".css")), "Built HTML has no CSS asset");
await Promise.all(assetUrls.map((url) => readFile(distPathFromUrl(url))));

const swSource = await readFile(path.join(distDir, "sw.js"), "utf8");
new Function(swSource);

const listeners = new Map();
const cacheEntries = new Map();
const origin = "https://bass.test";
let offline = false;
const normalizeKey = (input) => new URL(typeof input === "string" ? input : input.url, origin).href;
const cache = {
  async match(input) {
    return cacheEntries.get(normalizeKey(input))?.clone();
  },
  async put(input, response) {
    cacheEntries.set(normalizeKey(input), response.clone());
  },
};
const cacheStorage = {
  async open() {
    return cache;
  },
  async keys() {
    return ["bass-shell-old", "unrelated-cache"];
  },
  async delete() {
    return true;
  },
};
const localFetch = async (input) => {
  if (offline) throw new TypeError("offline");
  const url = new URL(typeof input === "string" ? input : input.url, origin);
  const body = await readFile(distPathFromUrl(url.href));
  return new Response(body, { status: 200 });
};
const workerSelf = {
  location: { origin },
  clients: { claim: async () => undefined },
  skipWaiting: async () => undefined,
  addEventListener(type, listener) {
    listeners.set(type, listener);
  },
};
vm.runInNewContext(swSource, {
  URL,
  Request,
  Response,
  Set,
  caches: cacheStorage,
  fetch: localFetch,
  self: workerSelf,
});

let installWork;
listeners.get("install")({ waitUntil: (promise) => { installWork = promise; } });
await installWork;
for (const url of assetUrls) {
  assert(cacheEntries.has(new URL(url, origin).href), `Service worker did not cache ${url}`);
}

function dispatchFetch(request) {
  let responsePromise = null;
  const background = [];
  listeners.get("fetch")({
    request,
    respondWith: (promise) => { responsePromise = Promise.resolve(promise); },
    waitUntil: (promise) => { background.push(Promise.resolve(promise)); },
  });
  return { background, responsePromise };
}

for (const pathname of ["/api/status", "/sim/scenario", "/health", "/ready"]) {
  const result = dispatchFetch({ method: "GET", url: `${origin}${pathname}`, mode: "cors", destination: "" });
  assert(result.responsePromise === null, `Service worker must not intercept ${pathname}`);
}
const post = dispatchFetch({ method: "POST", url: `${origin}/api/unlock`, mode: "cors", destination: "" });
assert(post.responsePromise === null, "Service worker must not intercept device commands");

offline = true;
const navigation = dispatchFetch({ method: "GET", url: `${origin}/`, mode: "navigate", destination: "document" });
assert(navigation.responsePromise, "Service worker must handle app navigation");
const offlineShell = await navigation.responsePromise;
assert(offlineShell.ok, "Cached app shell was not available offline");

console.log(`PWA check passed: ${assetUrls.length} build assets, ${manifest.icons.length} icons, API network-only`);
