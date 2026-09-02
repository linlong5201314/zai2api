"""Embedded admin panel (single HTML, no build chain)."""
ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--fg:#e6e9ef;--dim:#8b93a5;--acc:#4f8cff;--ok:#3fb96f;--bad:#e0566a}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:24px}
h1{font-size:18px;margin-bottom:16px}
h2{font-size:14px;color:var(--dim);margin:20px 0 8px;text-transform:uppercase;letter-spacing:.06em}
.card{background:var(--card);border-radius:10px;padding:16px;margin-bottom:16px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
input,select,button{background:#0c0e12;color:var(--fg);border:1px solid #2a2f3a;border-radius:6px;padding:8px 10px;font-size:13px}
input:focus{outline:1px solid var(--acc)}
button{cursor:pointer;border-color:var(--acc);color:var(--acc)}
button.warn{border-color:var(--bad);color:var(--bad)}
button:hover{filter:brightness(1.2)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #232833}
th{color:var(--dim);font-weight:500}
.st-active{color:var(--ok)}.st-invalid{color:var(--bad)}.st-cooling{color:#e0a856}
#login{max-width:360px;margin:10vh auto}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.kv .item{background:#0c0e12;border-radius:8px;padding:10px}
.kv .num{font-size:20px;font-weight:600}
.kv .lbl{color:var(--dim);font-size:12px}
#logs td{font-family:ui-monospace,monospace;font-size:12px}
.hidden{display:none}
</style>
</head>
<body>
<div id="login" class="card">
  <h1>zai2api admin</h1>
  <div class="row">
    <input id="pwd" type="password" placeholder="admin password" onkeydown="if(event.key==='Enter')doLogin()">
    <button onclick="doLogin()">Login</button>
  </div>
</div>
<div id="panel" class="hidden">
  <h1>zai2api admin</h1>
  <div class="card"><h2>Overview (24h)</h2><div id="stats" class="kv"></div></div>
  <div class="card"><h2>Upstream tokens</h2>
    <div class="row" style="margin-bottom:8px">
      <input id="ntoken" style="flex:1;min-width:280px" placeholder="eyJ... (Z.ai JWT from localStorage)">
      <select id="nkind"><option value="account">account</option><option value="guest">guest</option></select>
      <button onclick="addToken()">Add</button>
    </div>
    <table id="toktbl"><thead><tr><th>Token</th><th>Kind</th><th>Status</th><th>Uses</th><th>Errors</th><th></th></tr></thead><tbody></tbody></table>
  </div>
  <div class="card"><h2>Recent requests</h2>
    <div class="row" style="margin-bottom:8px"><button onclick="loadAll()">Refresh</button><span id="hint" style="color:var(--dim)"></span></div>
    <table id="logs"><thead><tr><th>Time</th><th>Model</th><th>Pool</th><th>Status</th><th>TTFT</th><th>Total</th><th>Error</th></tr></thead><tbody></tbody></table>
  </div>
</div>
<script>
const S={sid:''};
async function jpost(url,body,method='POST'){
  const r=await fetch(url,{method,headers:{'Content-Type':'application/json'},
    body:body?JSON.stringify(body):undefined,credentials:'same-origin'});
  if(r.status===401&&url!=='/admin/login'){showLogin();throw new Error('unauthorized')}
  return r;
}
async function doLogin(){
  const r=await jpost('/admin/login',{password:document.getElementById('pwd').value});
  if(r.ok){showPanel()}else{alert('wrong password')}
}
function showLogin(){document.getElementById('login').classList.remove('hidden');document.getElementById('panel').classList.add('hidden')}
function showPanel(){document.getElementById('login').classList.add('hidden');document.getElementById('panel').classList.remove('hidden');loadAll()}
async function addToken(){
  const token=document.getElementById('ntoken').value.trim();
  if(!token)return;
  const r=await jpost('/admin/api/tokens',{token,kind:document.getElementById('nkind').value});
  if(r.ok){document.getElementById('ntoken').value='';loadAll()}else{const e=await r.json();alert(e.detail||'failed')}
}
async function delToken(hint){
  if(!confirm('delete '+hint+' ?'))return;
  await jpost('/admin/api/tokens',{token:hint},'DELETE');
  loadAll();
}
async function loadAll(){
  const st=await (await fetch('/admin/api/stats',{credentials:'same-origin'})).json();
  const u=st.usage,p=st.pool;
  document.getElementById('stats').innerHTML=[
    ['Requests 24h',u.requests_24h],['OK 24h',u.ok_24h],
    ['Avg TTFT',u.avg_ttft_ms+' ms'],['Avg total',u.avg_latency_ms+' ms'],
    ['Accounts',p.accounts_active+'/'+p.accounts],['Guests',p.guests_active+'/'+p.guests]
  ].map(([l,v])=>`<div class="item"><div class="num">${v}</div><div class="lbl">${l}</div></div>`).join('');
  const toks=(await (await fetch('/admin/api/tokens',{credentials:'same-origin'})).json()).tokens;
  document.querySelector('#toktbl tbody').innerHTML=toks.map(t=>
    `<tr><td>${t.token_hint}</td><td>${t.kind}</td>
     <td class="st-${t.status}">${t.status}</td><td>${t.uses}</td><td>${t.errors}</td>
     <td><button class="warn" onclick="delToken('${t.token_hint}')">del</button></td></tr>`).join('')
    ||'<tr><td colspan="6" style="color:var(--dim)">no tokens</td></tr>';
  const logs=(await (await fetch('/admin/api/logs?limit=50',{credentials:'same-origin'})).json()).logs;
  document.querySelector('#logs tbody').innerHTML=logs.map(l=>
    `<tr><td>${new Date(l.ts*1000).toLocaleTimeString()}</td><td>${l.model||''}</td>
     <td>${l.token_kind||''}</td><td>${l.status}</td><td>${l.ttft_ms??'-'}</td>
     <td>${l.latency_ms??'-'}</td><td style="color:var(--bad)">${l.error||''}</td></tr>`).join('')
    ||'<tr><td colspan="7" style="color:var(--dim)">no requests yet</td></tr>';
}
(async()=>{
  const r=await fetch('/admin/session',{credentials:'same-origin'});
  if(r.ok)showPanel();else showLogin();
})();
</script>
</body>
</html>"""
