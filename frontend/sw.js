// Service Worker de AVAMET Monitor.
// __BUILD__ lo sustituye el servidor por un hash del contenido del frontend: cada vez que cambia algún
// fichero (index.html, sw.js, librerías de vendor/...) este fichero cambia, el navegador instala el SW nuevo,
// éste borra las cachés antiguas y la página se recarga sola (ver el registro en index.html).
const CACHE_NAME = 'avamet-__BUILD__';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((claves) => Promise.all(claves.filter((c) => c !== CACHE_NAME).map((c) => caches.delete(c))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  // Solo GET del propio servidor; el flujo en tiempo real y la API van SIEMPRE a la red (nunca datos cacheados).
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname === '/stream' || url.pathname.startsWith('/api/') || url.pathname.startsWith('/management/')) return;

  // Red primero (saltándose la caché HTTP del navegador para la página y el SW); la caché solo sirve de
  // respaldo si el servidor no responde.
  const esPagina = req.mode === 'navigate' || url.pathname === '/' || url.pathname === '/sw.js';
  e.respondWith(
    fetch(req, esPagina ? { cache: 'no-cache' } : undefined)
      .then((resp) => {
        if (resp && resp.ok) {
          const copia = resp.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, copia)).catch(() => {});
        }
        return resp;
      })
      .catch(() => caches.match(req))
  );
});
