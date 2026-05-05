// ===== projekt_detail.js =====
(async () => {
  "use strict";

  // 1) Auth-Guard + Header + apiFetch bereitstellen
  const { user, apiFetch } = await initDashboard({ requireAnyRole: ["manager", "admin"] });

  const $  = (s, p=document) => p.querySelector(s);

  // Projekt-ID holen (aus ?id=...)
  const url = new URL(location.href);
  const projectId =
    url.searchParams.get("id") ||
    url.searchParams.get("pid") ||
    (url.hash.startsWith("#id=") ? url.hash.slice(4) : null);

  if (!projectId) {
    alert("Fehlende Projekt-ID. Bitte über das Dashboard öffnen.");
    location.href = "/dashboard_manager.html";
  }

  function todayISO(){
    const d = new Date();
    return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10);
  }
  function minutesStr(m){
    m = +m || 0;
    return m < 60 ? `${m}m` : `${Math.floor(m/60)}h ${m%60}m`;
  }

  const selUser   = $("#staffSelect");
  const selType   = $("#typeSelect");
  const btnStart  = $("#btnStart");
  const btnSaveM  = $("#btnSaveManual");
  const btnEnd    = $("#btnEndProject");
  const mBox      = $("#manualBox");
  const mDate     = $("#mDate");
  const mHours    = $("#mHours");
  const mMins     = $("#mMins");
  const mComment  = $("#mComment");

  async function loadProject() {
    const r = await apiFetch(`/projekt/${encodeURIComponent(projectId)}`);
    const j = await r.json();
    if (j.status !== "success") {
      alert(j.message || "Projekt konnte nicht geladen werden.");
      location.href = "/dashboard_manager.html";
      return;
    }
    const p = j.project;
    $("#pName").textContent  = p.name;
    $("#pBadge").textContent = p.status;
    $("#pMeta").textContent  = `${p.company_name || "—"} — gestartet: ${p.start_am || "—"}`;
    $("#btnReport").href     = `/projekt/${encodeURIComponent(projectId)}/bericht`;
  }

  async function loadStaff() {
    selUser.innerHTML = `<option value="">(lade…)</option>`;
    try {
      const r = await apiFetch(`/projekt/${encodeURIComponent(projectId)}/mitglieder`);
      const j = await r.json();
      let items = (j && j.items) || [];

      // Falls keine Projekt-Mitglieder gepflegt sind → Fallback: aktueller User
      if (!items.length && user && user.id) {
        items = [{ id: user.id, name: user.name || user.email || "Ich", email: user.email }];
      }

      selUser.innerHTML = "";
      if (!items.length) {
        selUser.innerHTML = `<option value="">(keine Nutzer)</option>`;
        return;
      }
      for (const u of items) {
        const o = document.createElement("option");
        o.value = u.id;
        o.textContent = u.name || u.email || u.id;
        selUser.appendChild(o);
      }
    } catch (e) {
      console.error("[Staff] load error:", e);
      selUser.innerHTML = `<option value="">(Fehler beim Laden)</option>`;
    }
  }

  function setStartButton(running){
    btnStart.dataset.running = running ? "1" : "0";
    btnStart.textContent     = running ? "Stoppen" : "Starten";
  }
  async function updateRunningState() {
    const uid = selUser.value;
    if (!uid) { setStartButton(false); return; }
    const r = await apiFetch(`/projekt/${encodeURIComponent(projectId)}/laufend?user_id=${encodeURIComponent(uid)}`);
    const j = await r.json();
    setStartButton(!!j.running);
  }

  async function loadTimeline() {
    const r = await apiFetch(`/projekt/${encodeURIComponent(projectId)}/verlauf`);
    const j = await r.json();
    const tb = document.getElementById("sessBody");
    tb.innerHTML = "";
    const rows = j.items || [];
    if (!rows.length) {
      tb.innerHTML = `<tr><td colspan="6" class="muted">Noch keine Einträge.</td></tr>`;
      return;
    }
    rows.forEach(x => {
      const tr = document.createElement("tr");
      const typTxt = x.typ + (x.manuell ? " (manuell)" : "") + (x.kommentar ? ` – ${x.kommentar}` : "");
      tr.innerHTML = `
        <td>${x.benutzer_name || "—"}</td>
        <td>${typTxt}</td>
        <td>${x.start_am || ""}</td>
        <td>${x.ende_am || ""}</td>
        <td style="text-align:right">${x.ende_am ? minutesStr(x.dauer_min || 0) : "läuft…"}</td>
        <td></td>`;
      tb.appendChild(tr);
    });
  }

  function openManualWithDefaults(){
    mBox.style.display = "block";
    mDate.value  = todayISO();
    mHours.value = "6"; // Default 6h bei "Sonstige"
    mMins.value  = "0";
  }
  function closeManual(){
    mBox.style.display = "none";
  }
  selType.addEventListener("change", () => {
    if (selType.value === "anderes") openManualWithDefaults();
    else closeManual();
  });

  // Start/Stop
  btnStart.addEventListener("click", async () => {
    const uid = selUser.value;
    if (!uid) { alert("Bitte Mitarbeiter wählen."); return; }

    const running = btnStart.dataset.running === "1";
    if (running) {
      const r = await apiFetch(`/projekt/${encodeURIComponent(projectId)}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: uid })
      });
      const j = await r.json();
      if (j.status !== "success") { alert(j.message || "Stop fehlgeschlagen"); return; }
      await updateRunningState();
      await loadTimeline();
    } else {
      const typ = selType.value;
      if (typ === "anderes") { openManualWithDefaults(); return; }
      const r = await apiFetch(`/projekt/${encodeURIComponent(projectId)}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: uid, typ })
      });
      const j = await r.json();
      if (j.status !== "success") { alert(j.message || "Start fehlgeschlagen"); return; }
      await updateRunningState();
      await loadTimeline();
    }
  });

  // Manuell speichern
  document.getElementById("mDate").value = todayISO();
  btnSaveM.addEventListener("click", async () => {
    const uid = selUser.value;
    if (!uid) { alert("Bitte Mitarbeiter wählen."); return; }
    const d = mDate.value || todayISO();
    const h = parseFloat(mHours.value || "0");
    const m = parseInt(mMins.value || "0", 10);
    const k = (mComment.value || "").trim();
    if ((!h || h < 0) && (!m || m <= 0)) { alert("Bitte Dauer angeben."); return; }

    const r = await apiFetch(`/projekt/${encodeURIComponent(projectId)}/manuell`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: uid, datum_local: d, stunden: h, minuten: m, kommentar: k })
    });
    const j = await r.json();
    if (j.status !== "success") { alert(j.message || "Speichern fehlgeschlagen"); return; }
    mHours.value = ""; mMins.value  = ""; mComment.value = ""; closeManual();
    await loadTimeline();
  });

  // Projekt beenden
  async function projektBeendenBestaetigen() {
  try {
    // Rolle explizit mitsenden, damit /projekt/<id>/beenden uns erlaubt
    await postJSON(`/projekt/${PROJEKT_ID}/beenden`, {
      role: "manager",
      roles: ["manager"]
    });

    closeModal('projektBeendenModal');
    showAlert('✅ Projekt beendet', 'success');

    // optional: direkt zum Dashboard zurück
    setTimeout(() => {
      window.location.href = "/dashboard";
    }, 800);
  } catch (err) {
    showAlert('❌ ' + (err.message || 'Fehler beim Beenden'));
  }
}

  document.getElementById("staffSelect").addEventListener("change", updateRunningState);

  // Initial laden:
  await loadProject();
  await loadStaff();
  await updateRunningState();
  await loadTimeline();

  // Falls beim ersten Laden "Sonstige" ausgewählt ist:
  if (selType && selType.value === "anderes") openManualWithDefaults(); else closeManual();
})();
