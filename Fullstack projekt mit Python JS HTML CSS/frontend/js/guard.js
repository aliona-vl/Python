// frontend/js/guard.js
// Nutzt NUR das Cookie-Token. Kein Authorization-Header nötig.

(function () {
  async function fetchMe() {
    try {
      const res = await fetch("/api/auth/me", { credentials: "same-origin" });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || json?.ok === false) return null;
      return json?.data?.user || null;
    } catch {
      return null;
    }
  }

  // Global verfügbar
  window.requireRole = async function (roles) {
    const user = await fetchMe();
    if (!user) {
      alert("Nicht angemeldet.");
      location.href = "/";
      return;
    }
    const list = Array.isArray(roles) ? roles : [roles];
    if (roles && !list.includes(user.role)) {
      alert("Keine Berechtigung für diese Seite.");
      location.href = "/";
      return;
    }
    // Für weitere Seiten-Skripte nützlich:
    window.CURRENT_USER = user;
  };

  window.requireLoggedIn = async function () {
    return window.requireRole(["admin", "manager", "worker"]);
  };
})();
