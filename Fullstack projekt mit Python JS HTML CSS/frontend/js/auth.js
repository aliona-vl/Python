(async () => {
  try {
    const res = await fetch("http://127.0.0.1:5000/api/auth/me", { credentials: "same-origin" });
    const me  = await res.json().catch(() => ({}));

    const path = location.pathname.replace(/\/+$/, "");
    const onIndex     = (path === "" || path === "/" || path.endsWith("/index.html"));
    const onDashboard = (path === "/dashboard_manager" || path.endsWith("/dashboard_manager.html"));

    if (me?.ok) {
      // Nach Login zum Manager Dashboard
      if (onIndex && !location.hash) {
        location.hash = "manager";  // ← HIER GEÄNDERT
      }
      return;
    }

    // Nicht eingeloggt: zurück zur Startseite
    if (onDashboard) {
      location.replace("/");
    }
  } catch (e) {
    console.warn("auth.js check failed:", e);
  }
})();