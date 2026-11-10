// Fluent Ops – çevrimdışı önbellek. Yeni sürüm yüklediğinde VERSION'u artır.
const VERSION = "fo-pkg-v6";
const CORE = ["./", "./index.html", "./ceviri.html", "./news-voa.js", "./reading-content.js", "./manifest.json", "./icon-180.png", "./icon-192.png", "./icon-512.png"];
const DATA = ["./data/ACC.js", "./data/BUS.js", "./data/CIV.js", "./data/EXE.js", "./data/FIN.js", "./data/GEN.js", "./data/GRM.js", "./data/HRM.js", "./data/IND.js", "./data/KS1.js", "./data/KS2.js", "./data/KS3.js", "./data/LAW.js", "./data/LOG.js", "./data/MEC.js", "./data/MED.js", "./data/NVH.js", "./data/SAL.js", "./data/SWD.js", "./data/TRV.js", "./mtr-content.js"];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(CORE).then(() => Promise.all(DATA.map(u => c.add(u).catch(() => {}))))).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return; // Google/Drive/Gemini istekleri önbelleğe alınmaz
  const isData = url.pathname.includes("/data/") || url.pathname.endsWith("mtr-content.js") || url.pathname.endsWith("reading-content.js");
  if (isData) { // büyük içerik: önce önbellek
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request).then(n => { const cp = n.clone(); caches.open(VERSION).then(c => c.put(e.request, cp)); return n; })));
    return;
  }
  e.respondWith(fetch(e.request).then(r => { const cp = r.clone(); caches.open(VERSION).then(c => c.put(e.request, cp)); return r; })
    .catch(() => caches.match(e.request).then(r => r || caches.match("./index.html"))));
});
