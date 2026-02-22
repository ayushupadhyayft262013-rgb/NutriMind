const CACHE_NAME = "nutrimind-v1";
const ASSETS_TO_CACHE = [
    "/",
    "/dashboard",
    "/login",
    "/manifest.json",
    "/static/manifest.json",
];

// Install event: cache core assets
self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log("Opened cache");
            // Use catch() to prevent failing install if an asset fails to load
            return cache.addAll(ASSETS_TO_CACHE).catch(err => console.error("Cache add missing asset error:", err));
        })
    );
    self.skipWaiting();
});

// Activate event: clean up old caches
self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
    self.clients.claim();
});

// Fetch event: Network-first approach for HTML/JSON (to get fresh data), Cache-first for static assets
self.addEventListener("fetch", (event) => {
    const requestUrl = new URL(event.request.url);

    // Skip API calls for caching
    if (requestUrl.pathname.startsWith('/api/') || requestUrl.pathname.startsWith('/webhook/')) {
        return;
    }

    event.respondWith(
        fetch(event.request)
            .then((response) => {
                // Only cache valid responses
                if (!response || response.status !== 200 || response.type !== "basic") {
                    return response;
                }
                const responseToCache = response.clone();
                caches.open(CACHE_NAME).then((cache) => {
                    cache.put(event.request, responseToCache);
                });
                return response;
            })
            .catch(() => {
                // Fallback to cache if offline
                return caches.match(event.request);
            })
    );
});
