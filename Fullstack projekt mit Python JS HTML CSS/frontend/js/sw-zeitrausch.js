// =============================
// ZeitRausch Service Worker
// Für zuverlässige Hintergrund-Benachrichtigungen
// =============================

const CACHE_NAME = 'zeitrausch-v1';
const CHECK_INTERVAL = 30000; // 30 Sekunden

// Installation
self.addEventListener('install', (event) => {
  console.log('✅ Service Worker installiert');
  self.skipWaiting();
});

// Activation
self.addEventListener('activate', (event) => {
  console.log('✅ Service Worker aktiviert');
  event.waitUntil(clients.claim());
});

// Notification Click Handler
self.addEventListener('notificationclick', (event) => {
  console.log('🔔 Notification geklickt:', event.notification.tag);

  event.notification.close();

  const projektId = event.notification.data?.projektId;
  const url = projektId ? `/projekt/${projektId}` : '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        // Versuche existierendes Fenster zu fokussieren
        for (const client of clientList) {
          if (client.url.includes(url) && 'focus' in client) {
            return client.focus();
          }
        }
        // Sonst öffne neues Fenster
        if (clients.openWindow) {
          return clients.openWindow(url);
        }
      })
  );
});

// Notification Close Handler
self.addEventListener('notificationclose', (event) => {
  console.log('❌ Notification geschlossen:', event.notification.tag);
});

// Message Handler (für Kommunikation mit Main Thread)
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'CHECK_SESSIONS') {
    checkOverdueSessions();
  }
});

// Periodische Überprüfung (nur wenn Browser aktiv)
async function checkOverdueSessions() {
  try {
    const response = await fetch('/api/sessions/active');
    if (!response.ok) return;

    const data = await response.json();
    if (!data.ok || !data.data || data.data.length === 0) return;

    const now = Date.now();
    const THRESHOLD_MINUTES = 60;

    for (const session of data.data) {
      const start = new Date(session.start_zeit);
      if (isNaN(start.getTime())) continue;

      const diffMin = (now - start.getTime()) / 60000;
      if (diffMin < THRESHOLD_MINUTES) continue;

      const mitarbeiter = session.mitarbeiter || 'Mitarbeiter';
      const teilbereich = session.teilbereich || 'Tätigkeit';
      const projektName = session.projekt_name || 'Projekt';
      const projektId = session.projekt_id;

      const stunden = Math.floor(diffMin / 60);
      const minuten = Math.floor(diffMin % 60);
      const zeitText = stunden > 0 ? `${stunden}h ${minuten}m` : `${minuten}m`;

      // Notification senden
      await self.registration.showNotification(
        '⚠️ R-Time – Sitzung läuft über 1 Stunde',
        {
          body: `${projektName}\n${mitarbeiter} – ${teilbereich}\nLäuft seit: ${zeitText}`,
          tag: `zeitrausch-overdue-${projektId}-${session.start_zeit}`,
          icon: '/favicon.ico',
          badge: '/favicon.ico',
          requireInteraction: false,
          renotify: true,
          silent: false,
          data: {
            projektId: projektId,
            url: `/projekt/${projektId}`
          }
        }
      );
    }
  } catch (error) {
    console.error('Service Worker Check Fehler:', error);
  }
}

console.log('🚀 ZeitRausch Service Worker geladen');