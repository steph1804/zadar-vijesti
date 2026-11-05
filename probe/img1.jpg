// Uvijek prvo mreža; spremljena kopija služi samo kad nema veze (npr. na trajektu).
// Sprema se jedna kopija po stranici, bez ?v=/?r= oznaka (inače bi se svaka provjera
// svježine spremala zasebno i memorija bi rasla). v2 briše stare, napuhane spremnike.
const CACHE = "idassa-v2";

self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim())));

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.searchParams.has("v")) return;  // provjera svježine ide ravno na mrežu
  const key = url.origin + url.pathname;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && r.type === "basic") { const copy = r.clone(); caches.open(CACHE).then(c => c.put(key, copy)); }
    return r;
  }).catch(() => caches.match(key).then(r => r || new Response(
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><p style='font:16px sans-serif;padding:24px'>Nema internetske veze. Pokušaj ponovno za trenutak.</p>",
    { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }))));
});
