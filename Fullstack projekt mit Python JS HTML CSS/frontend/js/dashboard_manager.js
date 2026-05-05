// frontend/js/dashboard_manager.js
(async () => {
  "use strict";

  const { user } = await initDashboard({ requireAnyRole: ["manager","admin"] });

  const $  = (s, p=document) => p.querySelector(s);
  const $$ = (s, p=document) => Array.from(p.querySelectorAll(s));
  const withCreds = (opt={}) => Object.assign({ credentials: "same-origin" }, opt);

  const API = {
    companies: "/api/companies",
    staff: "/api/staff",
    projList: "/projekte",             // kompatible Routen aus Manager-Blueprint
    projNew: "/projekt/neu",
    projBulkDelete: "/projekt/bulk-delete",
    projClose: (id)=>`/projekt/${id}/beenden`,
    projDetailUrl: (id)=>`/projekt_details.html?id=${encodeURIComponent(id)}`,
    reportSummary: "/gesamt-bericht"
  };

  // ---------- Panel-Toggle ----------
  const panels = {
    companies: $("#companiesPanel"),
    staff: $("#staffPanel"),
    reports: $("#reportsPanel")
  };
  function showPanel(name){
    Object.values(panels).forEach(el => el.style.display = "none");
    panels[name].style.display = "";
  }
  $("#openCompanies").addEventListener("click", ()=>{ showPanel("companies"); renderCompaniesPanel(); });
  $("#openStaff").addEventListener("click",     ()=>{ showPanel("staff");     renderStaffPanel(); });
  $("#openReports").addEventListener("click",   ()=>{ showPanel("reports");   initReports(); });

  // ---------- Unternehmen ----------
  async function fetchCompanies(){
    const r = await fetch(API.companies, withCreds()); const j = await r.json();
    if (!j.ok) throw new Error(j?.error?.message || "Fehler");
    return j.data.companies || [];
  }
  async function fillCompanySelect(){
    const list = await fetchCompanies();
    const sel = $("#companySelect");
    sel.innerHTML = "";
    list.forEach(c => {
      const o = document.createElement("option");
      o.value = c.id; o.textContent = c.name;
      sel.appendChild(o);
    });
  }
  async function renderCompaniesPanel(){
    await fillCompanySelect();  // damit das "Projekt starten" Select aktuell ist
    const tb = $("#companiesBody");
    tb.innerHTML = `<tr><td colspan="2" class="muted">Lade …</td></tr>`;
    const list = await fetchCompanies();
    if (!list.length){
      tb.innerHTML = `<tr><td colspan="2" class="muted">Keine Unternehmen.</td></tr>`;
      return;
    }
    tb.innerHTML = "";
    for (const c of list){
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>
          <span class="coName" data-id="${c.id}" title="Zum Umbenennen doppelklicken">${c.name}</span>
        </td>
        <td style="text-align:right">
          <button class="btn danger coDel" data-id="${c.id}">Löschen</button>
        </td>`;
      tb.appendChild(tr);
    }
    // Löschen
    $$(".coDel", tb).forEach(btn=>{
      btn.addEventListener("click", async ()=>{
        const id = btn.getAttribute("data-id");
        if(!confirm("Wirklich löschen? (nur ohne Projekte möglich)")) return;
        const r = await fetch(`${API.companies}/${encodeURIComponent(id)}`, withCreds({ method:"DELETE" }));
        const j = await r.json();
        if(!j.ok){ alert(j?.error?.message || "Löschen fehlgeschlagen"); return; }
        await renderCompaniesPanel();
      });
    });
    // Umbenennen
    $$(".coName", tb).forEach(span=>{
      span.addEventListener("dblclick", async ()=>{
        const id = span.getAttribute("data-id");
        const current = span.textContent.trim();
        const name = prompt("Neuer Name:", current);
        if (!name || name === current) return;
        const r = await fetch(`${API.companies}/${encodeURIComponent(id)}`, withCreds({
          method: "PUT",
          headers: { "Content-Type":"application/json" },
          body: JSON.stringify({ name })
        }));
        const j = await r.json();
        if(!j.ok){ alert(j?.error?.message || "Umbenennen fehlgeschlagen"); return; }
        await renderCompaniesPanel();
      });
    });
  }
  $("#btnAddCompany").addEventListener("click", async ()=>{
    const name = $("#newCompanyName").value.trim();
    if (!name){ alert("Name erforderlich"); return; }
    const r = await fetch(API.companies, withCreds({
      method: "POST", headers: { "Content-Type":"application/json" }, body: JSON.stringify({ name })
    }));
    const j = await r.json();
    if (!j.ok){ alert(j?.error?.message || "Anlegen fehlgeschlagen"); return; }
    $("#newCompanyName").value = "";
    await renderCompaniesPanel();
  });

  // ---------- Mitarbeiter (Manager/Admin) ----------
  async function fetchStaff(){
    const r = await fetch(API.staff, withCreds()); const j = await r.json();
    if (!j.ok) throw new Error(j?.error?.message || "Fehler");
    return j.data.items || [];
  }
  async function renderStaffPanel(){
    const tb = $("#staffBody");
    tb.innerHTML = `<tr><td colspan="3" class="muted">Lade …</td></tr>`;
    const list = await fetchStaff();
    if (!list.length){
      tb.innerHTML = `<tr><td colspan="3" class="muted">Keine Mitarbeiter (Manager/Admin) gefunden.</td></tr>`;
      return;
    }
    tb.innerHTML = "";
    for (const u of list){
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${u.full_name || "—"}</td>
        <td>${u.email || "—"}</td>
        <td style="text-align:right">
          <button class="btn ${u.is_active ? "danger" : ""} stToggle" data-id="${u.id}" data-active="${u.is_active ? 1 : 0}">
            ${u.is_active ? "Deaktivieren" : "Aktivieren"}
          </button>
        </td>`;
      tb.appendChild(tr);
    }
    // Toggle aktiv
    $$(".stToggle", tb).forEach(btn=>{
      btn.addEventListener("click", async ()=>{
        const id = btn.getAttribute("data-id");
        const active = btn.getAttribute("data-active") === "1";
        const r = await fetch(`${API.staff}/${encodeURIComponent(id)}`, withCreds({
          method: "PUT",
          headers: { "Content-Type":"application/json" },
          body: JSON.stringify({ is_active: !active })
        }));
        const j = await r.json();
        if (!j.ok){ alert(j?.error?.message || "Änderung fehlgeschlagen"); return; }
        await renderStaffPanel();
      });
    });
  }

  // ---------- Projekte (Liste/Anlegen/Bulk-Delete) ----------
  function minutesStr(m){ m=+m||0; return m<60?`${m}m`:`${Math.floor(m/60)}h ${m%60}m`; }

  const selected = new Set();
  function updateBulkBar(){
    $("#bulkCount").textContent = selected.size;
    $("#bulkBar").style.display = selected.size ? "flex" : "none";
  }

  async function reloadProjects(){
    const p={}; const st=$("#filterStatus").value; if(st) p.status=st;
    const q=$("#filterQ").value.trim(); if(q) p.q=q;
    const qs = Object.keys(p).length ? ("?"+new URLSearchParams(p)) : "";
    const r = await fetch(API.projList+qs, withCreds());
    const j = await r.json();
    const items = j?.items || j?.data?.items || [];
    renderProjectCards(items);
    $("#projectsInfo").textContent = `${items.length} Projekt(e)`;
  }

  function renderProjectCards(items){
    const wrap=$("#projectsGrid"); wrap.innerHTML="";
    if(!items.length){ wrap.innerHTML=`<div class="muted">Keine Projekte gefunden.</div>`; updateBulkBar(); return; }
    items.forEach(p=>{
      const el=document.createElement("article"); el.className="project-card";
      const total=minutesStr(p.total_minutes||0);
      const checked=selected.has(p.id)?"checked":"";
      el.innerHTML=`
        <header style="display:flex; align-items:center; gap:.6rem">
          <h3 style="flex:1">${p.name}</h3>
          <span class="badge">${p.status}</span>
          <label class="chk" style="margin-left:auto; display:flex; align-items:center; gap:.4rem">
            <input type="checkbox" class="pchk" data-id="${p.id}" ${checked}>
            <span class="muted">auswählen</span>
          </label>
        </header>
        <div class="muted">Unternehmen: ${p.company_name||"—"}</div>
        <div class="muted">Gesamtdauer: <b>${total}</b></div>
        <div class="card-actions">
          <a class="btn" href="${API.projDetailUrl(p.id)}">Verwalten</a>
          ${p.status!=="beendet" ? `<button class="btn danger closeBtn" data-id="${p.id}">Beenden</button>` : ""}
        </div>`;
      wrap.appendChild(el);

      el.querySelector(".pchk").addEventListener("change",(ev)=>{
        const id=ev.target.getAttribute("data-id");
        if(ev.target.checked) selected.add(id); else selected.delete(id);
        updateBulkBar();
      });
      const endBtn = el.querySelector(".closeBtn");
      if (endBtn){
        endBtn.addEventListener("click", async ()=>{
          if(!confirm("Projekt beenden?")) return;
          const r = await fetch(API.projClose(endBtn.getAttribute("data-id")), withCreds({ method:"POST" }));
          const j = await r.json(); if(!j.ok) return alert(j?.error?.message || "Fehler");
          await reloadProjects();
        });
      }
    });
    updateBulkBar();
  }

  // Neues Projekt
  $("#formProject").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name      = $("#projectName").value.trim();
    const companyId = $("#companySelect").value || "";
    const msg       = $("#createMsg");
    if (!name || !companyId) { msg.textContent = "Bitte Name & Unternehmen wählen."; return; }

    msg.textContent = "Lege an …";
    const r = await fetch(API.projNew, withCreds({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, company_id: companyId })
    }));
    const j = await r.json();
    const id = j?.id || j?.data?.id;
    if (j?.ok && id) { location.href = API.projDetailUrl(id); return; }
    msg.textContent = j?.error?.message || "Fehler"; setTimeout(() => (msg.textContent = ""), 1200);
  });

  // Bulk Delete
  $("#btnBulkDelete").addEventListener("click", async ()=>{
    if (!selected.size) return;
    if (!confirm(`Wirklich ${selected.size} Projekt(e) löschen?`)) return;
    const ids = Array.from(selected).map(x=>+x);
    const r = await fetch(API.projBulkDelete, withCreds({
      method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ projekt_ids: ids })
    }));
    const j = await r.json();
    if (!j.ok){ alert(j?.error?.message || "Löschen fehlgeschlagen"); return; }
    selected.clear();
    await reloadProjects();
  });

  // ---------- Berichte ----------
  function isoDate(d){ return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10); }
  function initReports(){
    const to = new Date();
    const from = new Date(to.getFullYear(), to.getMonth(), 1);
    $("#fromDate").value = $("#fromDate").value || isoDate(from);
    $("#toDate").value = $("#toDate").value || isoDate(to);
  }
  $("#btnOpenSummary").addEventListener("click", ()=>{
    const von = $("#fromDate").value; const bis = $("#toDate").value;
    const qs = new URLSearchParams({ von, bis }).toString();
    window.open(`${API.reportSummary}?${qs}`, "_blank");
  });

  // ---------- Init ----------
  await fillCompanySelect();
  await reloadProjects();
})();
