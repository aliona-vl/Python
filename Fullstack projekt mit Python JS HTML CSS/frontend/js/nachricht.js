// =============================
// ZeitRausch – Sitzungs-Erinnerung
// Funktioniert auf ALLEN Seiten + im Hintergrund
// =============================

// Konfiguration
const THRESHOLD_MINUTES = 60;   // ab 60 Minuten erinnern
const REPEAT_EVERY_MIN  = 15;    // Wiederholung alle 15 Minuten
const CHECK_INTERVAL_VISIBLE = 60000;   // 60s wenn sichtbar
const CHECK_INTERVAL_HIDDEN  = 30000;   // 30s wenn versteckt

// Zustand pro Sitzung
const overdueState = new Map();
let notificationPermissionRequested = false;
let checkIntervalId = null;

// ----- Notification-Berechtigung -----
async function ensureNotificationPermission() {
  if (!("Notification" in window)) {
    console.log("Browser unterstützt keine Notifications");
    return false;
  }

  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;

  if (notificationPermissionRequested) {
    return Notification.permission === "granted";
  }

  notificationPermissionRequested = true;
  const perm = await Notification.requestPermission();
  return perm === "granted";
}

// ----- Seite versteckt? -----
function isPageHidden() {
  return document.hidden || document.visibilityState === 'hidden';
}

// ----- Aktive Sitzungen vom Server holen -----
async function checkOverdueSessionsAPI() {
  try {
    const response = await fetch('/api/sessions/active');
    if (!response.ok) {
      console.log('❌ Konnte Sitzungen nicht laden');
      return;
    }

    const data = await response.json();
    if (!data.ok || !data.data || data.data.length === 0) {
      removeInAppWarning();
      return;
    }

    const now = Date.now();

    data.data.forEach(session => {
      const start = new Date(session.start_zeit);
      if (isNaN(start.getTime())) return;

      const diffMin = (now - start.getTime()) / 60000;
      if (diffMin < THRESHOLD_MINUTES) return;

      const mitarbeiter = session.mitarbeiter || 'Mitarbeiter';
      const teilbereich = session.teilbereich || 'Tätigkeit';
      const projektName = session.projekt_name || 'Projekt';
      const projektId = session.projekt_id;

      const key = `${projektId}|${mitarbeiter}|${teilbereich}|${session.start_zeit}`;
      maybeNotifyForSession(key, mitarbeiter, teilbereich, diffMin, projektName, projektId);
    });

  } catch (error) {
    console.error('Fehler beim Prüfen:', error);
  }
}

// ----- Benachrichtigung auslösen -----
function maybeNotifyForSession(key, mitarbeiter, teilbereich, diffMin, projektName, projektId) {
  const now = Date.now();
  let state = overdueState.get(key);

  if (!state) {
    state = { lastNotifiedAt: 0, handled: false };
    overdueState.set(key, state);
  }

  if (state.handled) return;

  const minutesSinceLast = state.lastNotifiedAt > 0
    ? (now - state.lastNotifiedAt) / 60000
    : Number.POSITIVE_INFINITY;

  if (minutesSinceLast < REPEAT_EVERY_MIN) return;

  state.lastNotifiedAt = now;

  // Im Hintergrund NUR Desktop, sonst beides
  if (isPageHidden()) {
    showDesktopNotification(mitarbeiter, teilbereich, diffMin, key, state, projektName, projektId);
  } else {
    showInAppWarning(mitarbeiter, teilbereich, diffMin, key, state, projektName, projektId);
    showDesktopNotification(mitarbeiter, teilbereich, diffMin, key, state, projektName, projektId);
  }
}

// ----- Desktop-Notification (VERBESSERT für Hintergrund) -----
function showDesktopNotification(mitarbeiter, teilbereich, diffMin, key, state, projektName, projektId) {
  ensureNotificationPermission().then(ok => {
    if (!ok) return;

    const stunden = Math.floor(diffMin / 60);
    const minuten = Math.floor(diffMin % 60);
    const zeitText = stunden > 0 ? `${stunden}h ${minuten}m` : `${minuten}m`;

    // KRITISCHE ÄNDERUNGEN für Hintergrund-Notifications:
    const options = {
      body: `${projektName}\n${mitarbeiter} – ${teilbereich}\nLäuft seit: ${zeitText}`,
      tag: "zeitrausch-overdue-" + key,
      icon: "/favicon.ico",
      badge: "/favicon.ico",
      // WICHTIG: requireInteraction auf false, damit es auch minimiert funktioniert
      requireInteraction: false,
      // renotify muss true sein für wiederholte Benachrichtigungen
      renotify: true,
      // Zusätzliche Optionen für bessere Sichtbarkeit
      silent: false,
      // Daten für Click-Handler
      data: {
        projektId: projektId,
        key: key,
        url: `/projekt/${projektId}`
      }
    };

    try {
      const n = new Notification(
        "⚠️ R-Time – Sitzung läuft über 1 Stunde",
        options
      );

      n.onclick = () => {
        state.handled = true;

        // Browser-Fenster in den Vordergrund bringen
        if (window.parent) {
          window.parent.focus();
        }
        window.focus();

        // Zur Projektseite navigieren
        window.location.href = `/projekt/${projektId}`;
        n.close();
        removeInAppWarning();
      };

      n.onclose = () => {
        // Nur handled setzen, wenn User die Notification schließt
        // NICHT automatisch, damit sie bei Wiederholung neu erscheint
      };

      n.onerror = (e) => {
        console.error('Notification Fehler:', e);
      };

      // Auto-close nach 10 Sekunden (außer bei requireInteraction)
      setTimeout(() => {
        try {
          n.close();
        } catch (e) {
          // Ignorieren wenn bereits geschlossen
        }
      }, 10000);

    } catch (error) {
      console.error('Fehler beim Erstellen der Notification:', error);
    }
  });
}

// ----- In-App-Banner -----
function showInAppWarning(mitarbeiter, teilbereich, diffMin, key, state, projektName, projektId) {
  let bar = document.getElementById("zr-overdue-bar");

  if (!bar) {
    bar = document.createElement("div");
    bar.id = "zr-overdue-bar";
    bar.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      z-index: 9999;
      padding: 12px 16px;
      background: linear-gradient(135deg, #ff5050 0%, #ff3030 100%);
      color: #fff;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      font-size: 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      box-shadow: 0 2px 8px rgba(0,0,0,0.3);
      animation: slideDown 0.3s ease;
    `;

    const text = document.createElement("span");
    text.id = "zr-overdue-text";
    text.style.flex = "1";
    text.style.fontWeight = "600";

    const btnOpen = document.createElement("button");
    btnOpen.textContent = "Zum Projekt";
    btnOpen.style.cssText = `
      margin-left: 12px;
      padding: 8px 16px;
      border: none;
      border-radius: 4px;
      background: #1a1a1a;
      color: #fff;
      cursor: pointer;
      font-weight: 600;
      font-size: 13px;
      transition: background 0.2s;
    `;
    btnOpen.onmouseover = () => btnOpen.style.background = '#000';
    btnOpen.onmouseout = () => btnOpen.style.background = '#1a1a1a';
    btnOpen.onclick = () => {
      state.handled = true;
      window.location.href = `/projekt/${projektId}`;
      removeInAppWarning();
    };

    const btnClose = document.createElement("button");
    btnClose.textContent = "✕";
    btnClose.style.cssText = `
      margin-left: 8px;
      padding: 6px 10px;
      border: none;
      border-radius: 4px;
      background: rgba(255,255,255,0.2);
      color: #fff;
      cursor: pointer;
      font-weight: bold;
      font-size: 16px;
      transition: background 0.2s;
    `;
    btnClose.onmouseover = () => btnClose.style.background = 'rgba(255,255,255,0.3)';
    btnClose.onmouseout = () => btnClose.style.background = 'rgba(255,255,255,0.2)';
    btnClose.onclick = () => {
      state.handled = true;
      removeInAppWarning();
    };

    const right = document.createElement("div");
    right.style.display = "flex";
    right.style.gap = "8px";
    right.appendChild(btnOpen);
    right.appendChild(btnClose);

    bar.appendChild(text);
    bar.appendChild(right);
    document.body.appendChild(bar);
  }

  const t = document.getElementById("zr-overdue-text");
  if (t) {
    const stunden = Math.floor(diffMin / 60);
    const minuten = Math.floor(diffMin % 60);
    const zeitText = stunden > 0 ? `${stunden}h ${minuten}m` : `${minuten}m`;

    t.textContent = `⚠️ ${projektName} – ${mitarbeiter} (${teilbereich}) läuft seit ${zeitText}`;
  }
}

function removeInAppWarning() {
  const bar = document.getElementById("zr-overdue-bar");
  if (bar && bar.parentNode) {
    bar.parentNode.removeChild(bar);
  }
}

// ----- Intervall dynamisch anpassen -----
function updateCheckInterval() {
  if (checkIntervalId) {
    clearInterval(checkIntervalId);
  }

  const interval = isPageHidden() ? CHECK_INTERVAL_HIDDEN : CHECK_INTERVAL_VISIBLE;
  checkIntervalId = setInterval(checkOverdueSessionsAPI, interval);

  console.log(`🔔 Check-Intervall: ${interval/1000}s (${isPageHidden() ? 'versteckt' : 'sichtbar'})`);
}

// ----- Visibility Change Listener -----
document.addEventListener('visibilitychange', () => {
  console.log(`👁️ Sichtbarkeit geändert: ${document.visibilityState}`);

  if (isPageHidden()) {
    console.log('🔕 Seite versteckt - prüfe für Desktop-Notifications');
    checkOverdueSessionsAPI();
  } else {
    console.log('👀 Seite sichtbar - zeige In-App-Warnungen');
    checkOverdueSessionsAPI();
  }

  updateCheckInterval();
});

// ----- Page Focus Event (zusätzlich) -----
window.addEventListener('focus', () => {
  console.log('🎯 Fenster hat Focus erhalten');
  checkOverdueSessionsAPI();
});

window.addEventListener('blur', () => {
  console.log('💤 Fenster hat Focus verloren');
});

// ----- Initiales Setup -----
// Berechtigung nach 5 Sekunden anfragen (nur wenn aktive Sitzungen)
setTimeout(async () => {
  try {
    const res = await fetch('/api/sessions/active');
    const data = await res.json();
    if (data.ok && data.data && data.data.length > 0) {
      const granted = await ensureNotificationPermission();
      if (granted) {
        console.log('✅ Notification-Berechtigung erteilt');
      } else {
        console.log('⚠️ Notification-Berechtigung verweigert');
      }
    }
  } catch (e) {
    console.log('Konnte Berechtigung nicht prüfen');
  }
}, 5000);

// Erstes Check nach 10 Sekunden
setTimeout(checkOverdueSessionsAPI, 10000);

// Intervall starten
updateCheckInterval();

// Debug-Funktionen
window.zrCheckOverdue = checkOverdueSessionsAPI;
window.zrTestNotify = () => {
  if (Notification.permission !== "granted") {
    console.log("❌ Keine Berechtigung für Notifications");
    return;
  }

  const n = new Notification("🧪 Test R-Time", {
    body: "Das ist eine Test-Benachrichtigung\nKlicke hier zum Testen",
    requireInteraction: false,
    icon: "/favicon.ico",
    tag: "test-notification"
  });

  n.onclick = () => {
    console.log("✅ Notification geklickt!");
    window.focus();
    n.close();
  };

  console.log("🔔 Test-Notification gesendet");
};

console.log('✅ ZeitRausch Benachrichtigungen aktiv (alle Seiten + Hintergrund)');