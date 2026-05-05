// frontend/js/common.js
const API_BASE = "http://127.0.0.1:5000/api";

// Kein localStorage-Token mehr – wir nutzen das HttpOnly-Cookie
async function apiMe() {
  const r = await fetch(`${API_BASE}/auth/me`, { credentials: "same-origin" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j?.ok === false) throw new Error(j?.error?.message || "auth");
  const user = j?.data?.user || j?.user || {};
  const roles = user.role ? [user.role] : [];
  return { user, roles };
}

function logout() {
  fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "same-origin" })
    .finally(() => (location.href = "/"));
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderTopbar({ user }) {
  const host = document.getElementById("appHeader");
  if (!host) return;
  const displayName = (user?.full_name || user?.name || user?.email || "Benutzer");
  host.innerHTML = `
    <div class="topbar">
      <div class="brand"><span class="logo">⏱</span> <span>ZeitRausch</span></div>
      <div class="spacer"></div>
      <div class="userbox">
        <span class="username">${esc(displayName)}</span>
        <button id="btnLogout" class="btn btn-ghost">Abmelden</button>
      </div>
    </div>`;
  document.getElementById("btnLogout").addEventListener("click", logout);
}

/**
 * Initialisiert ein Dashboard:
 * - prüft /api/auth/me (Cookie wird automatisch gesendet)
 * - rendert Topbar
 * - prüft Rollen (optional)
 * @param {{requireAnyRole?: string[]}} opts
 * @returns {Promise<{user: any, roles: string[]}>}
 */
async function initDashboard(opts = {}) {
  try {
    const me = await apiMe();
    renderTopbar(me);

    const need = opts.requireAnyRole || [];
    if (need.length) {
      const have = me.roles || (me.user?.role ? [me.user.role] : []);
      const ok = need.some((r) => have.includes(r));
      if (!ok) {
        alert("Keine Berechtigung für diese Seite.");
        location.href = "/";
        throw new Error("forbidden");
      }
    }
    return me;
  } catch (e) {
    location.href = "/";
    throw e;
  }
}
