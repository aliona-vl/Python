// frontend/js/dashboard_worker.js
(async () => {
  await initDashboard({ requireAnyRole: ["worker","manager","admin"] });

  const withCreds = (opt={}) => Object.assign({ credentials: "same-origin" }, opt);

  async function load(){
    // Firmenliste
    let res = await fetch('/api/companies', withCreds());
    let j   = await res.json();
    const firms = j?.data?.companies || j?.companies || [];
    document.getElementById('firma').innerHTML =
      firms.map(f=>`<option value="${f.id}">${f.name}</option>`).join('');

    // Meine Einträge (falls vorhanden)
    res = await fetch('/api/entries/me', withCreds());
    const rows = await res.json().catch(()=>({items:[]}));
    render(rows?.items || rows || []);
  }

  function render(rows){
    const tb = document.querySelector('#tbl tbody'); tb.innerHTML=''; let sum=0;
    for(const r of rows){
      const h = Number(r.stunden || (r.minutes||0)/60);
      sum += h;
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${r.datum || r.date || ''}</td>
        <td>${(r.unternehmen_id||r.company_id||'').toString().slice(0,8)}…</td>
        <td>${h.toFixed(2)}</td>
        <td>${r.kommentar || r.comment || ''}</td>
        <td>${r.bezahlt ? 'bezahlt' : (r.paid?'bezahlt':'offen')}</td>`;
      tb.appendChild(tr);
    }
    document.getElementById('sum').innerText = `Summe: ${sum.toFixed(2)} h`;
  }

  document.getElementById('save').onclick = async ()=>{
    const row = {
      company_id: document.getElementById('firma').value,
      datum_local: document.getElementById('datum').value,
      stunden: parseFloat(document.getElementById('stunden').value||'0'),
      kommentar: document.getElementById('komm').value
    };
    let res = await fetch('/api/entries', withCreds({
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(row)
    }));
    let d = await res.json();
    if(!d.ok) return alert(d?.error?.message || 'Fehler beim Speichern');
    res = await fetch('/api/entries/me', withCreds()); const j = await res.json();
    render(j?.items || j || []);
  };

  load();
})();
