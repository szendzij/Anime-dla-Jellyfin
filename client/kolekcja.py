#!/usr/bin/env python3
"""Panel „Kolekcja" — wyszukaj anime i dodaj je do biblioteki Jellyfin.

    python3 kolekcja.py --port 9100 \
        --root ~/media/anime \
        --base-url http://192.168.1.20:9099

Panel nie odtwarza wideo — robi to Jellyfin. Jego zadaniem jest wyszukanie serialu,
wygenerowanie plików .strm i poproszenie Jellyfina o skan biblioteki.

Dwa źródła: ogladajanime (wymaga logowania) oraz CDA (bez logowania, przyjmuje
również wklejony link do folderu z odcinkami).

Jellyfin (opcjonalnie, do automatycznego skanu):
    export JELLYFIN_URL=http://192.168.1.10:8096
    export JELLYFIN_API_KEY=...
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import collection
from oa import AuthError, OgladajAnimeError
from providers import CDA, OA, ProviderError, build_sources
from providers.cda import normalize_ref

PAGE = """<!doctype html>
<html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Kolekcja Anime</title>
<style>
 :root{--bg:#0d0f14;--surface:#151821;--card:#1c202c;--border:#282d3e;--fg:#edf0f7;--mut:#8e96aa;--acc:#5c7cfa;--acc-hover:#4c6ef5;--ok:#38d9a9;--warn:#ff922b;--danger:#ff6b6b}
 *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
 body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,sans-serif;padding:max(16px,env(safe-area-inset-top)) max(16px,env(safe-area-inset-right)) max(28px,env(safe-area-inset-bottom)) max(16px,env(safe-area-inset-left));min-height:100vh;display:flex;flex-direction:column;align-items:center}
 .app{width:100%;max-width:960px}
 header{margin-bottom:18px}
 h1{font-size:22px;font-weight:700;margin:0 0 4px;display:flex;align-items:center;gap:8px}
 .sub{color:var(--mut);font-size:13px;margin:0}
 .tabs{display:flex;gap:6px;background:var(--surface);padding:4px;border-radius:12px;border:1px solid var(--border);margin-bottom:18px;overflow-x:auto}
 .tab-btn{flex:1;min-width:100px;min-height:44px;padding:10px 14px;border:0;border-radius:8px;background:transparent;color:var(--mut);font-size:14px;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;white-space:nowrap;transition:all .15s ease}
 .tab-btn.active{background:var(--card);color:var(--fg);box-shadow:0 2px 8px rgba(0,0,0,0.25)}
 .tab-badge{background:rgba(92,124,250,0.2);color:var(--acc);font-size:11px;padding:2px 7px;border-radius:99px}
 .form-row{display:flex;gap:10px;margin-bottom:14px}
 input,select{padding:12px 14px;border-radius:10px;border:1px solid var(--border);background:var(--surface);color:var(--fg);font-size:16px;outline:none;transition:border-color .15s ease}
 input:focus,select:focus{border-color:var(--acc)}
 input{flex:1;min-width:0} select{cursor:pointer}
 button{min-height:44px;padding:12px 20px;border-radius:10px;border:0;background:var(--acc);color:#fff;font-size:15px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:6px;transition:background .15s ease,filter .15s ease}
 button:hover{background:var(--acc-hover)} button:active{filter:brightness(0.95)} button:disabled{opacity:.55;cursor:not-allowed}
 button.ghost{background:var(--surface);color:var(--fg);border:1px solid var(--border)}
 button.ghost:hover{background:var(--card)}
 button.danger{background:rgba(255,107,107,0.15);color:var(--danger);border:0}
 button.danger:hover{background:var(--danger);color:#fff}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:14px;margin-top:14px}
 .card{background:var(--card);border-radius:12px;border:1px solid var(--border);overflow:hidden;display:flex;flex-direction:column}
 .card-img-wrap{width:100%;aspect-ratio:2/3;background:#11131a;position:relative;overflow:hidden}
 .card img{width:100%;height:100%;object-fit:cover;display:block}
 .card .b{padding:10px;display:flex;flex-direction:column;gap:6px;flex:1}
 .card .t{font-size:13px;font-weight:600;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:35px}
 .card .m{color:var(--mut);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .card button{margin-top:auto;padding:8px;font-size:13px;min-height:38px}
 .col-header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap}
 .col-search-wrap{flex:1;min-width:200px}
 .item{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px;display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap}
 .item .n{flex:1;min-width:0}
 .item .n b{display:block;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:3px}
 .item .n span{color:var(--mut);font-size:12px}
 .tag{font-size:11px;font-weight:600;padding:3px 8px;border-radius:99px;white-space:nowrap}
 .tag.ok{background:rgba(56,217,169,0.15);color:var(--ok)}
 .tag.cda{background:rgba(92,124,250,0.15);color:var(--acc)}
 .tag.shinden{background:rgba(255,146,43,0.15);color:var(--warn)}
 .empty{text-align:center;padding:36px 16px;color:var(--mut);font-size:14px}
 .toast-container{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);display:flex;flex-direction:column;gap:8px;z-index:9999;max-width:92vw;pointer-events:none}
 .toast{background:var(--card);border:1px solid var(--border);color:var(--fg);padding:12px 18px;border-radius:10px;font-size:14px;box-shadow:0 4px 16px rgba(0,0,0,0.4);animation:slideUp .25s ease-out;pointer-events:auto;display:flex;align-items:center;gap:8px}
 .toast.err{border-color:var(--danger);color:#ff8787}
 .toast.ok{border-color:var(--ok);color:#63e6be}
 @keyframes slideUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
 @media (max-width:640px){
  body{padding:max(12px,env(safe-area-inset-top)) 12px max(20px,env(safe-area-inset-bottom)) 12px}
  h1{font-size:19px}
  .form-row{flex-direction:column}
  .form-row select,.form-row input,.form-row button{width:100%}
  .col-header{flex-direction:column;align-items:stretch}
  .item{flex-direction:column;align-items:flex-start;gap:10px}
  .item-actions{width:100%;display:flex;justify-content:flex-end}
  .item-actions button{width:100%}
  .grid{grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px}
  .card .t{font-size:12px;min-height:32px}
 }
</style></head><body>
<div class="app">
 <header>
  <h1>🎬 Kolekcja Anime</h1>
  <p class="sub">Zarządzanie biblioteką .strm dla serwera Jellyfin</p>
 </header>

 <nav class="tabs">
  <button class="tab-btn active" data-tab="tab-search">🔍 Szukaj</button>
  <button class="tab-btn" data-tab="tab-link">🔗 Link CDA</button>
  <button class="tab-btn" data-tab="tab-col">📚 Kolekcja <span class="tab-badge" id="col-badge">0</span></button>
 </nav>

 <main id="tab-search" class="tab-pane">
  <div class="form-row">
   <select id="src">
    <option value="oa">ogladajanime</option>
    <option value="cda">CDA</option>
    <option value="shinden">Shinden</option>
   </select>
   <input id="q" placeholder="np. frieren, one piece, dragon ball…" autocomplete="off">
   <button id="sb">Szukaj</button>
  </div>
  <div class="grid" id="res"></div>
 </main>

 <main id="tab-link" class="tab-pane" style="display:none">
  <p class="sub" style="margin-bottom:12px">Wklej link do folderu CDA (<code>cda.pl/&lt;user&gt;/folder/12345</code>) lub pojedynczego filmu.</p>
  <div class="form-row">
   <input id="url" placeholder="https://www.cda.pl/…/folder/…" autocomplete="off">
   <button id="ub">Dodaj do biblioteki</button>
  </div>
 </main>

 <main id="tab-col" class="tab-pane" style="display:none">
  <div class="col-header">
   <div class="col-search-wrap">
    <input id="col-filter" placeholder="Filtruj kolekcję…" autocomplete="off" style="width:100%">
   </div>
   <button class="ghost" id="scan">🔄 Skanuj Jellyfin</button>
  </div>
  <div id="col"></div>
 </main>
</div>

<div class="toast-container" id="toasts"></div>

<script>
const $=s=>document.querySelector(s);
const $$=s=>document.querySelectorAll(s);
const BASE=window.location.pathname.endsWith('/')?window.location.pathname:window.location.pathname+'/';

function toast(text,type='info'){
  const c=$('#toasts');
  const t=document.createElement('div');
  t.className='toast '+(type==='err'?'err':(type==='ok'?'ok':''));
  t.textContent=text;
  c.appendChild(t);
  setTimeout(()=>{
    t.style.opacity='0';
    t.style.transition='opacity 0.3s';
    setTimeout(()=>t.remove(),300);
  },4000);
}

async function api(endpoint,body){
  const url=BASE+endpoint.replace(/^\\//,'');
  const r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const j=await r.json().catch(()=>({error:'Nieprawidłowa odpowiedź serwera'}));
  if(!r.ok) throw new Error(j.error||('HTTP '+r.status));
  return j;
}

function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

// Tabs
$$('.tab-btn').forEach(b=>{
  b.onclick=()=>{
    $$('.tab-btn').forEach(x=>x.classList.remove('active'));
    $$('.tab-pane').forEach(x=>x.style.display='none');
    b.classList.add('active');
    $('#'+b.dataset.tab).style.display='block';
    if(b.dataset.tab==='tab-col') loadCol();
  };
});

async function search(){
  const q=$('#q').value.trim(); if(!q) return;
  const src=$('#src').value;
  $('#sb').disabled=true;
  $('#sb').textContent='Szukam…';
  $('#res').innerHTML='<div class="empty">Przeszukiwanie bazy…</div>';
  try{
    const d=await api('api/search?source='+encodeURIComponent(src)+'&q='+encodeURIComponent(q));
    if(!d.length){
      $('#res').innerHTML='<div class="empty">Brak wyników dla podanej frazy.</div>';
    }else{
      $('#res').innerHTML=d.map(i=>`<div class="card">
        <div class="card-img-wrap"><img loading="lazy" src="${esc(i.poster||'')}" alt="" onerror="this.style.opacity=0"></div>
        <div class="b"><div class="t" title="${esc(i.title)}">${esc(i.title)}</div>
        <div class="m">${esc(i.kind)} · ${esc(i.ref)}</div>
        <button data-source="${esc(src)}" data-ref="${esc(i.ref)}" data-title="${esc(i.title)}" data-poster="${esc(i.poster||'')}">Dodaj</button></div></div>`).join('');
      $('#res').querySelectorAll('button').forEach(b=>b.onclick=()=>add(b));
      toast(`Znaleziono ${d.length} pozycji (${src})`,'ok');
    }
  }catch(e){
    $('#res').innerHTML='<div class="empty" style="color:var(--danger)">Błąd wyszukiwania: '+esc(e.message)+'</div>';
    toast('Błąd: '+e.message,'err');
  }finally{
    $('#sb').disabled=false;
    $('#sb').textContent='Szukaj';
  }
}

async function add(btn){
  btn.disabled=true;
  const originalText=btn.textContent;
  btn.textContent='Dodawanie…';
  toast('Generowanie plików dla '+btn.dataset.title+'…');
  try{
    const d=await api('api/add',{source:btn.dataset.source,ref:btn.dataset.ref,title:btn.dataset.title,poster:btn.dataset.poster});
    toast(`${d.title}: zapisano ${d.files} odcinków`+(d.scanned?', skan Jellyfin uruchomiony':''),'ok');
    btn.textContent='Dodano ✓';
    loadCol();
  }catch(e){
    toast('Błąd: '+e.message,'err');
    btn.disabled=false;
    btn.textContent=originalText;
  }
}

async function addUrl(){
  const url=$('#url').value.trim(); if(!url) return;
  $('#ub').disabled=true;
  $('#ub').textContent='Dodawanie…';
  toast('Rozpoznawanie linku CDA…');
  try{
    const d=await api('api/link',{url});
    toast(`${d.title}: zapisano ${d.files} odcinków`+(d.scanned?', skan Jellyfin uruchomiony':''),'ok');
    $('#url').value='';
    loadCol();
  }catch(e){
    toast('Błąd: '+e.message,'err');
  }finally{
    $('#ub').disabled=false;
    $('#ub').textContent='Dodaj do biblioteki';
  }
}

async function loadCol(){
  try{
    const d=await api('api/collection');
    const ks=Object.keys(d);
    $('#col-badge').textContent=ks.length;
    if(!ks.length){
      $('#col').innerHTML='<div class="empty">Kolekcja jest pusta. Dodaj serial w zakładce Szukaj lub Link CDA.</div>';
      return;
    }
    $('#col').innerHTML=ks.map(k=>{
      const e=d[k];
      const s=e.source||'oa';
      const tagClass=s==='cda'?'cda':(s==='shinden'?'shinden':'ok');
      const tagLabel=s==='cda'?'CDA':(s==='shinden'?'Shinden':'ogladajanime');
      return `<div class="item" data-title="${esc(e.title)}" id="item-${esc(k)}">
        <span class="tag ${tagClass}">${esc(tagLabel)}</span>
        <div class="n">
          <b>${esc(e.title)}</b>
          <span>${e.episodes||0} odcinków · dodano ${(e.added||'').slice(0,10)}${e.last_sync?' · sync '+(e.last_sync||'').slice(0,10):''}</span>
        </div>
        <div class="item-actions">
          <button class="ghost" data-ep-key="${esc(k)}">Odcinki</button>
          <button class="danger" data-key="${esc(k)}">Usuń</button>
        </div>
        <div class="ep-container" id="ep-container-${esc(k)}" style="display:none;width:100%;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
          <div class="ep-loading" style="color:var(--mut);font-size:13px">Ładowanie odcinków…</div>
          <div class="ep-list" style="display:flex;flex-direction:column;gap:6px"></div>
        </div>
      </div>`;
    }).join('');
    $('#col').querySelectorAll('button.danger').forEach(b=>b.onclick=()=>rm(b.dataset.key));
    $('#col').querySelectorAll('button[data-ep-key]').forEach(b=>b.onclick=()=>toggleEpisodes(b.dataset.epKey, b));
  }catch(e){
    toast('Nie udało się załadować kolekcji: '+e.message,'err');
  }
}

async function toggleEpisodes(key, btn){
  const container = document.getElementById('ep-container-' + key);
  if (!container) return;
  if (container.style.display !== 'none') {
    container.style.display = 'none';
    btn.textContent = 'Odcinki';
    return;
  }
  container.style.display = 'block';
  btn.textContent = 'Zwiń';
  const listEl = container.querySelector('.ep-list');
  const loadingEl = container.querySelector('.ep-loading');
  loadingEl.style.display = 'block';
  loadingEl.textContent = 'Ładowanie listy odcinków…';
  listEl.innerHTML = '';
  try {
    const data = await api('api/series_episodes?key=' + encodeURIComponent(key));
    loadingEl.style.display = 'none';
    if (!data.episodes || !data.episodes.length) {
      listEl.innerHTML = '<div style="color:var(--mut);font-size:13px">Brak odcinków w źródle.</div>';
      return;
    }
    listEl.innerHTML = data.episodes.map(ep => {
      const pText = ep.player_idx !== null && ep.player_idx !== undefined
        ? `<span class="tag ok" style="font-size:10px;padding:2px 6px">Odtwarzacz #${ep.player_idx}</span>`
        : `<span class="tag" style="font-size:10px;padding:2px 6px;color:var(--mut)">Domyślny</span>`;
      const epNum = ep.number !== null && ep.number !== undefined ? ep.number : 0;
      const code = `S${String(ep.season).padStart(2,'0')}E${String(epNum).padStart(2,'0')}`;
      return `<div class="ep-row" style="display:flex;align-items:center;justify-content:space-between;gap:8px;background:var(--card);padding:8px 12px;border-radius:8px;font-size:13px;flex-wrap:wrap">
        <div style="flex:1;min-width:180px;display:flex;align-items:center;gap:6px">
          <b>${esc(code)}</b>
          <span style="color:var(--fg);overflow:hidden;text-overflow:ellipsis">${esc(ep.title || 'Odcinek')}</span>
          ${pText}
        </div>
        <div>
          <button class="ghost" style="min-height:30px;padding:4px 10px;font-size:12px" data-pchoose-key="${esc(key)}" data-pchoose-ep="${esc(ep.ep_id)}" data-pchoose-src="${esc(data.source)}">Zmień odtwarzacz</button>
        </div>
        <div class="ep-players-box" id="ep-players-${esc(ep.ep_id)}" style="display:none;width:100%;margin-top:6px;padding-top:6px;border-top:1px dashed var(--border)"></div>
      </div>`;
    }).join('');
    listEl.querySelectorAll('button[data-pchoose-key]').forEach(b => {
      b.onclick = () => openPlayerChooser(b.dataset.pchooseKey, b.dataset.pchooseEp, b.dataset.pchooseSrc);
    });
  } catch (e) {
    loadingEl.style.display = 'block';
    loadingEl.textContent = 'Błąd: ' + e.message;
  }
}

async function openPlayerChooser(key, epId, source){
  const box = document.getElementById('ep-players-' + epId);
  if (!box) return;
  if (box.style.display !== 'none') {
    box.style.display = 'none';
    return;
  }
  box.style.display = 'block';
  box.innerHTML = '<span style="color:var(--mut);font-size:12px">Pobieranie dostępnych odtwarzaczy…</span>';
  try {
    const data = await api(`api/episode_players?source=${encodeURIComponent(source)}&ep_id=${encodeURIComponent(epId)}`);
    if (!data.players || !data.players.length) {
      box.innerHTML = '<span style="color:var(--warn);font-size:12px">Brak alternatywnych odtwarzaczy dla tego odcinka.</span>';
      return;
    }
    let html = `<div style="font-size:12px;color:var(--mut);margin-bottom:6px">Wybierz odtwarzacz dla tego odcinka:</div>`;
    html += `<div style="display:flex;gap:6px;flex-wrap:wrap">`;
    html += `<button class="ghost" style="min-height:28px;padding:3px 8px;font-size:11px" data-set-p="null">↺ Automatyczny (domyślny)</button>`;
    data.players.forEach(p => {
      const label = `[#${p.idx}] ${esc(p.label)} (${esc(p.host)})`;
      html += `<button class="ghost" style="min-height:28px;padding:3px 8px;font-size:11px" data-set-p="${p.idx}">${label}</button>`;
    });
    html += `</div>`;
    box.innerHTML = html;
    box.querySelectorAll('button[data-set-p]').forEach(b => {
      const pVal = b.dataset.setP === 'null' ? null : parseInt(b.dataset.setP, 10);
      b.onclick = () => setPlayer(key, epId, pVal);
    });
  } catch (e) {
    box.innerHTML = `<span style="color:var(--danger);font-size:12px">Błąd: ${esc(e.message)}</span>`;
  }
}

async function setPlayer(key, epId, playerIdx){
  toast('Zapisywanie wybranego odtwarzacza…');
  try {
    await api('api/set_episode_player', {key, ep_id: epId, player_idx: playerIdx});
    toast('Zmieniono odtwarzacz! Plik .strm został zaktualizowany.', 'ok');
    const container = document.getElementById('ep-container-' + key);
    const itemEl = document.getElementById('item-' + key);
    const btn = itemEl ? itemEl.querySelector('button[data-ep-key]') : null;
    if (container && btn) {
      container.style.display = 'none';
      toggleEpisodes(key, btn);
    }
  } catch (e) {
    toast('Błąd: ' + e.message, 'err');
  }
}

async function rm(key){
  if(!confirm('Usunąć "'+key+'" razem ze wszystkimi plikami .strm z dysku?')) return;
  try{
    await api('api/remove',{key});
    toast('Usunięto '+key,'ok');
    loadCol();
  }catch(e){
    toast('Błąd: '+e.message,'err');
  }
}

$('#col-filter').oninput=e=>{
  const term=e.target.value.toLowerCase().trim();
  $$('#col .item').forEach(el=>{
    const title=(el.dataset.title||'').toLowerCase();
    el.style.display=title.includes(term)?'':'none';
  });
};

$('#sb').onclick=search;
$('#ub').onclick=addUrl;
$('#q').addEventListener('keydown',e=>{if(e.key==='Enter') search()});
$('#url').addEventListener('keydown',e=>{if(e.key==='Enter') addUrl()});
$('#scan').onclick=async()=>{
  try{
    const d=await api('api/scan',{});
    toast(d.message || (d.scanned?'Skan Jellyfina został uruchomiony!':'Nie udało się uruchomić skanu Jellyfin'),d.scanned?'ok':'err');
  }catch(e){
    toast('Błąd skanowania: '+e.message,'err');
  }
};
loadCol();
</script></body></html>
"""


class State:
    def __init__(self, sources: dict, data: dict, path: Path, root: Path, base_url: str) -> None:
        self.sources = sources
        self.data = data
        self.path = path
        self.root = root
        self.base_url = base_url
        self.lock = threading.Lock()

    def get_data(self) -> dict:
        with self.lock:
            self.data = collection.load(self.path)
            return self.data


def build_handler(st: State):
    class H(BaseHTTPRequestHandler):
        server_version = "Kolekcja/1.1"

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            print(f"[panel] {self.address_string()} {fmt % args}", flush=True)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload) -> None:
            self._send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                return {}

        def _source(self, name: str):
            src = st.sources.get(name or OA)
            if src is None:
                raise OgladajAnimeError(f"nieznane źródło: {name}")
            return src

        def _endpoint(self) -> tuple[str, str]:
            u = urlparse(self.path)
            p = u.path.rstrip("/")
            ingress_path = (self.headers.get("X-Ingress-Path") or "").rstrip("/")
            if ingress_path and p.startswith(ingress_path):
                p = p[len(ingress_path):]
            return p or "/", u.query

        def do_GET(self) -> None:  # noqa: N802
            p, query = self._endpoint()
            if p in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                return
            if p == "/api/search":
                qs = parse_qs(query)
                q = (qs.get("q") or [""])[0].strip()
                name = (qs.get("source") or [OA])[0]
                if not q:
                    self._json(400, {"error": "brak frazy"})
                    return
                print(f"[panel] szukaj: '{q}' (źródło: {name})", flush=True)
                try:
                    src = self._source(name)
                    src.ensure_ready()
                    results = src.search(q)
                    print(f"[panel] znaleziono {len(results)} wyników dla '{q}'", flush=True)
                    self._json(200, results)
                except (AuthError, ProviderError) as e:
                    print(f"[panel] błąd wyszukiwania '{q}': {e}", flush=True)
                    self._json(502, {"error": str(e)})
                return
            if p == "/api/collection":
                self._json(200, st.get_data().get("series", {}))
                return
            if p == "/api/series_episodes":
                qs = parse_qs(query)
                key = (qs.get("key") or [""])[0].strip()
                if not key:
                    self._json(400, {"error": "brak parametru 'key'"})
                    return
                with st.lock:
                    st.data = collection.load(st.path)
                    entry = st.data.get("series", {}).get(key)
                if not entry:
                    self._json(404, {"error": f"Nie znaleziono serii '{key}'"})
                    return
                source_name = entry.get("source") or OA
                try:
                    src = self._source(source_name)
                    src.ensure_ready()
                    episodes = src.episodes(collection.entry_ref(key, entry))
                    overrides = entry.get("player_overrides", {})
                    out = [
                        {
                            "ep_id": str(e.ep_id),
                            "season": e.season,
                            "number": e.number,
                            "title": e.title,
                            "player_idx": overrides.get(str(e.ep_id)),
                        }
                        for e in episodes
                    ]
                    self._json(200, {"key": key, "source": source_name, "episodes": out})
                except (AuthError, ProviderError) as e:
                    print(f"[panel] błąd pobierania odcinków dla '{key}': {e}", flush=True)
                    self._json(502, {"error": str(e)})
                return
            if p == "/api/episode_players":
                qs = parse_qs(query)
                source_name = (qs.get("source") or [OA])[0].strip() or OA
                ep_id = (qs.get("ep_id") or [""])[0].strip()
                if not ep_id:
                    self._json(400, {"error": "brak parametru 'ep_id'"})
                    return
                try:
                    src = self._source(source_name)
                    src.ensure_ready()
                    links = src.links(ep_id)
                    players = []
                    for idx, l in enumerate(links):
                        host = ""
                        target = l.main_url or l.src or ""
                        if target:
                            try:
                                host = urlparse(target).netloc.lower()
                                if host.startswith("www."):
                                    host = host[4:]
                            except Exception:
                                host = ""
                        players.append({
                            "idx": idx,
                            "label": l.label,
                            "res": l.res,
                            "host": host or "domyślny CDN",
                            "mime": l.mime,
                        })
                    self._json(200, {"ep_id": ep_id, "players": players})
                except (AuthError, ProviderError) as e:
                    print(f"[panel] błąd pobierania odtwarzaczy dla '{ep_id}': {e}", flush=True)
                    self._json(502, {"error": str(e)})
                return
            self._json(404, {"error": "nieznana ścieżka"})

        def _add(self, source, ref: str, title: str, poster: str) -> dict:
            source.ensure_ready()
            print(f"[panel] dodawanie serii: '{title}' (ref: {ref}, źródło: {source.name})", flush=True)
            with st.lock:
                st.data = collection.load(st.path)
                entry = collection.add_series(
                    source, st.data, ref, title, st.root, st.base_url, poster
                )
                collection.save(st.path, st.data)
            files_cnt = len(entry["files"]) if isinstance(entry.get("files"), list) else entry.get("files", 0)
            print(f"[panel] zapisano serię '{title}' ({files_cnt} plików .strm)", flush=True)
            return entry

        def do_POST(self) -> None:  # noqa: N802
            p, _ = self._endpoint()
            body = self._body()

            if p == "/api/add":
                ref = (body.get("ref") or "").strip()
                title = (body.get("title") or ref).strip()
                if not ref:
                    self._json(400, {"error": "brak identyfikatora"})
                    return
                try:
                    src = self._source(body.get("source") or OA)
                    entry = self._add(src, ref, title, body.get("poster") or "")
                except (AuthError, ProviderError, OSError) as e:
                    print(f"[panel] błąd dodawania serii '{title}': {e}", flush=True)
                    self._json(502, {"error": str(e)})
                    return
                scanned = collection.trigger_scan()
                self._json(200, {"title": entry["title"], "files": entry["files"], "scanned": scanned})
                return

            if p == "/api/link":
                # Wklejony link CDA: rozpoznajemy folder albo pojedynczy film
                # i sami ustalamy tytuł serialu.
                raw_url = body.get("url") or ""
                print(f"[panel] dodawanie z linku: '{raw_url}'", flush=True)
                ref = normalize_ref(raw_url)
                if not ref:
                    self._json(400, {"error": "nie rozpoznano linku CDA (oczekiwano /…/folder/<id> albo /video/<id>)"})
                    return
                try:
                    src = self._source(CDA)
                    title = src.api.describe(ref)
                    entry = self._add(src, ref, title, "")
                except (ProviderError, OSError) as e:
                    print(f"[panel] błąd przetwarzania linku '{raw_url}': {e}", flush=True)
                    self._json(502, {"error": str(e)})
                    return
                scanned = collection.trigger_scan()
                self._json(200, {"title": entry["title"], "files": entry["files"], "scanned": scanned})
                return

            if p == "/api/remove":
                key = (body.get("key") or "").strip()
                print(f"[panel] usuwanie z kolekcji: '{key}'", flush=True)
                with st.lock:
                    st.data = collection.load(st.path)
                    ok = collection.remove_series(st.data, key)
                    collection.save(st.path, st.data)
                scanned = collection.trigger_scan() if ok else False
                print(f"[panel] usunięto serię '{key}': {'sukces' if ok else 'nie znaleziono'}", flush=True)
                self._json(200 if ok else 404, {"removed": ok, "scanned": scanned})
                return

            if p == "/api/set_episode_player":
                key = (body.get("key") or "").strip()
                ep_id = str(body.get("ep_id") or "").strip()
                player_idx = body.get("player_idx")
                if player_idx is not None:
                    try:
                        player_idx = int(player_idx)
                    except (ValueError, TypeError):
                        player_idx = None
                if not key or not ep_id:
                    self._json(400, {"error": "brak parametru 'key' lub 'ep_id'"})
                    return
                try:
                    with st.lock:
                        st.data = collection.load(st.path)
                        entry = st.data.get("series", {}).get(key)
                        if not entry:
                            self._json(404, {"error": f"Nie znaleziono serii '{key}'"})
                            return
                        src = self._source(entry.get("source") or OA)
                        src.ensure_ready()
                        url = collection.set_episode_player(st.data, key, ep_id, player_idx, src)
                        collection.save(st.path, st.data)
                    print(f"[panel] zmieniono odtwarzacz dla '{key}' ep_id {ep_id} -> {url}", flush=True)
                    self._json(200, {"ok": True, "url": url})
                except (AuthError, ProviderError, KeyError, ValueError, OSError) as e:
                    print(f"[panel] błąd zmiany odtwarzacza dla '{key}' ({ep_id}): {e}", flush=True)
                    self._json(502, {"error": str(e)})
                return

            if p == "/api/scan":
                print("[panel] żądanie skanowania biblioteki Jellyfin z interfejsu WWW", flush=True)
                ok, msg = collection.trigger_scan_status()
                self._json(200, {"scanned": ok, "message": msg})
                return

            self._json(404, {"error": "nieznana ścieżka"})

    return H


def main() -> int:
    ap = argparse.ArgumentParser(description="panel kolekcji anime dla Jellyfin")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--root", required=True, help="katalog biblioteki Jellyfin")
    ap.add_argument("--base-url", required=True, help="adres redirectora, np. http://IP:9099")
    ap.add_argument("--data", default="~/.config/ogladajanime/collection.json")
    ap.add_argument("--session-file", default=None)
    args = ap.parse_args()

    path = Path(args.data).expanduser()
    root = Path(args.root).expanduser()
    root.mkdir(parents=True, exist_ok=True)

    kw = {"session_file": args.session_file} if args.session_file else {}
    sources = build_sources(**kw)
    # Logowanie sprawdzamy dopiero przy pierwszym użyciu ogladajanime —
    # panel ma wstać także wtedy, gdy dostępne jest tylko CDA.
    try:
        sources[OA].ensure_ready()
    except AuthError as e:
        print(f"[panel] uwaga: ogladajanime nieaktywne ({e}) — zostaje CDA i Shinden", flush=True)

    st = State(sources, collection.load(path), path, root, args.base_url)
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), build_handler(st))
    except OSError as e:
        print(
            f"[panel] nie mogę zająć portu {args.host}:{args.port} ({e}). "
            f"Sprawdź, czy panel już nie działa: ss -lntp | grep {args.port}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    print(f"[panel] start panelu WWW: http://{args.host}:{args.port}/ (katalog: {root})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[panel] zatrzymano", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
