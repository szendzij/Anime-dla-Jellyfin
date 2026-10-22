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
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kolekcja anime</title>
<style>
 :root{--bg:#12141a;--card:#1b1e27;--fg:#e8eaf0;--mut:#8b93a7;--acc:#6c8cff;--ok:#3fbf7f}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;padding:24px}
 h1{font-size:20px;margin:0 0 4px}
 .sub{color:var(--mut);font-size:13px;margin-bottom:20px}
 .row{display:flex;gap:8px;max-width:820px}
 input,select{padding:12px 14px;border-radius:10px;border:1px solid #2c3140;background:#0e1015;color:var(--fg);font-size:15px}
 input{flex:1}
 button{padding:12px 18px;border-radius:10px;border:0;background:var(--acc);color:#fff;font-weight:600;cursor:pointer}
 button:hover{filter:brightness(1.1)} button:disabled{opacity:.5;cursor:default}
 button.ghost{background:#252a36}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px;margin-top:18px}
 .card{background:var(--card);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
 .card img{width:100%;aspect-ratio:128/180;object-fit:cover;background:#0e1015}
 .card .b{padding:10px;display:flex;flex-direction:column;gap:8px;flex:1}
 .t{font-size:13px;font-weight:600;line-height:1.3;min-height:34px}
 .m{color:var(--mut);font-size:11px;word-break:break-all}
 .card button{padding:8px;font-size:13px;margin-top:auto}
 section{margin-top:34px;max-width:1100px}
 h2{font-size:15px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.06em}
 .item{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #232733}
 .item .n{flex:1} .item .n b{display:block;font-size:14px}
 .item .n span{color:var(--mut);font-size:12px}
 .tag{font-size:11px;padding:3px 8px;border-radius:99px;background:#252a36;color:var(--mut)}
 .tag.ok{background:rgba(63,191,127,.15);color:var(--ok)}
 .tag.cda{background:rgba(108,140,255,.15);color:var(--acc)}
 .msg{color:var(--mut);font-size:13px;margin-top:10px;min-height:18px}
 .err{color:#ff7b7b}
</style></head><body>
<h1>Kolekcja anime</h1>
<div class="sub">Dodanie serialu generuje pliki .strm i uruchamia skan biblioteki Jellyfin.</div>
<div class="row">
  <select id="src">
    <option value="oa">ogladajanime</option>
    <option value="cda">CDA</option>
    <option value="shinden">Shinden</option>
  </select>
  <input id="q" placeholder="np. frieren, one piece, dragon ball…" autocomplete="off">
  <button id="sb">Szukaj</button>
</div>
<div class="msg" id="msg"></div>
<div class="grid" id="res"></div>

<section>
  <h2>Dodaj z linku CDA</h2>
  <div class="sub" style="margin-bottom:10px">Wklej adres folderu z odcinkami (np. cda.pl/&lt;user&gt;/folder/12345)
  albo pojedynczego odcinka.</div>
  <div class="row">
    <input id="url" placeholder="https://www.cda.pl/…/folder/…" autocomplete="off">
    <button id="ub">Dodaj</button>
  </div>
  <div class="msg" id="umsg"></div>
</section>

<section>
  <h2>W kolekcji</h2>
  <div id="col"></div>
  <div style="margin-top:14px"><button class="ghost" id="scan">Skanuj Jellyfin</button></div>
</section>

<script>
const $=s=>document.querySelector(s);
const msg=(t,e)=>{const m=$('#msg');m.textContent=t;m.className='msg'+(e?' err':'')};
const umsg=(t,e)=>{const m=$('#umsg');m.textContent=t;m.className='msg'+(e?' err':'')};
async function api(p,body){
  const r=await fetch(p,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const j=await r.json().catch(()=>({error:'zła odpowiedź'}));
  if(!r.ok) throw new Error(j.error||('HTTP '+r.status));
  return j;
}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function search(){
  const q=$('#q').value.trim(); if(!q) return;
  const src=$('#src').value;
  $('#sb').disabled=true; msg('Szukam…'); $('#res').innerHTML='';
  try{
    const d=await api('/api/search?source='+encodeURIComponent(src)+'&q='+encodeURIComponent(q));
    msg(d.length?`${d.length} wyników (${src})`:'Brak wyników');
    $('#res').innerHTML=d.map(i=>`<div class="card">
      <img loading="lazy" src="${esc(i.poster||'')}" alt="">
      <div class="b"><div class="t">${esc(i.title)}</div>
      <div class="m">${esc(i.kind)} · ${esc(i.ref)}</div>
      <button data-source="${esc(src)}" data-ref="${esc(i.ref)}" data-title="${esc(i.title)}" data-poster="${esc(i.poster||'')}">Dodaj</button></div></div>`).join('');
    $('#res').querySelectorAll('button').forEach(b=>b.onclick=()=>add(b));
  }catch(e){msg('Błąd: '+e.message,true)}
  $('#sb').disabled=false;
}
async function add(btn){
  btn.disabled=true; msg('Dodaję '+btn.dataset.title+'…');
  try{
    const d=await api('/api/add',{source:btn.dataset.source,ref:btn.dataset.ref,title:btn.dataset.title,poster:btn.dataset.poster});
    msg(`${d.title}: zapisano ${d.files} odcinków`+(d.scanned?', skan uruchomiony':', uruchom skan ręcznie'));
    loadCol();
  }catch(e){msg('Błąd: '+e.message,true); btn.disabled=false}
}
async function addUrl(){
  const url=$('#url').value.trim(); if(!url) return;
  $('#ub').disabled=true; umsg('Dodaję…');
  try{
    const d=await api('/api/link',{url});
    umsg(`${d.title}: zapisano ${d.files} odcinków`+(d.scanned?', skan uruchomiony':', uruchom skan ręcznie'));
    $('#url').value=''; loadCol();
  }catch(e){umsg('Błąd: '+e.message,true)}
  $('#ub').disabled=false;
}
async function loadCol(){
  const d=await api('/api/collection');
  const ks=Object.keys(d);
  $('#col').innerHTML=ks.length?ks.map(k=>{const e=d[k];const s=e.source||'oa';
    return `<div class="item">
    <span class="tag ${s==='cda'?'cda':'ok'}">${esc(s==='cda'?'CDA':'ogladajanime')}</span>
    <div class="n"><b>${esc(e.title)}</b><span>${e.episodes||0} odcinków · dodano ${(e.added||'').slice(0,10)}${e.last_sync?' · sync '+(e.last_sync||'').slice(0,10):''}</span></div>
    <button class="ghost" data-key="${esc(k)}">Usuń</button></div>`}).join(''):'<div class="msg">Nic jeszcze nie dodane.</div>';
  $('#col').querySelectorAll('button').forEach(b=>b.onclick=()=>rm(b.dataset.key));
}
async function rm(key){
  if(!confirm('Usunąć '+key+' razem z plikami?')) return;
  try{await api('/api/remove',{key}); msg('Usunięto '+key); loadCol()}catch(e){msg('Błąd: '+e.message,true)}
}
$('#sb').onclick=search;
$('#ub').onclick=addUrl;
$('#q').addEventListener('keydown',e=>{if(e.key==='Enter') search()});
$('#url').addEventListener('keydown',e=>{if(e.key==='Enter') addUrl()});
$('#scan').onclick=async()=>{try{const d=await api('/api/scan',{}); msg(d.scanned?'Skan uruchomiony':'Brak klucza API Jellyfina — uruchom skan w panelu Jellyfin',!d.scanned)}catch(e){msg('Błąd: '+e.message,true)}};
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


def build_handler(st: State):
    class H(BaseHTTPRequestHandler):
        server_version = "Kolekcja/1.1"

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            print(f"{self.address_string()} {fmt % args}", flush=True)

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

        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                return
            if u.path == "/api/search":
                qs = parse_qs(u.query)
                q = (qs.get("q") or [""])[0].strip()
                name = (qs.get("source") or [OA])[0]
                if not q:
                    self._json(400, {"error": "brak frazy"})
                    return
                try:
                    src = self._source(name)
                    src.ensure_ready()
                    self._json(200, src.search(q))
                except (AuthError, ProviderError) as e:
                    self._json(502, {"error": str(e)})
                return
            if u.path == "/api/collection":
                self._json(200, st.data["series"])
                return
            self._json(404, {"error": "nieznana ścieżka"})

        def _add(self, source, ref: str, title: str, poster: str) -> dict:
            source.ensure_ready()
            with st.lock:
                entry = collection.add_series(
                    source, st.data, ref, title, st.root, st.base_url, poster
                )
                collection.save(st.path, st.data)
            return entry

        def do_POST(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            body = self._body()

            if u.path == "/api/add":
                ref = (body.get("ref") or "").strip()
                title = (body.get("title") or ref).strip()
                if not ref:
                    self._json(400, {"error": "brak identyfikatora"})
                    return
                try:
                    src = self._source(body.get("source") or OA)
                    entry = self._add(src, ref, title, body.get("poster") or "")
                except (AuthError, ProviderError, OSError) as e:
                    self._json(502, {"error": str(e)})
                    return
                scanned = collection.trigger_scan()
                self._json(200, {"title": entry["title"], "files": entry["files"], "scanned": scanned})
                return

            if u.path == "/api/link":
                # Wklejony link CDA: rozpoznajemy folder albo pojedynczy film
                # i sami ustalamy tytuł serialu.
                ref = normalize_ref(body.get("url") or "")
                if not ref:
                    self._json(400, {"error": "nie rozpoznano linku CDA (oczekiwano /…/folder/<id> albo /video/<id>)"})
                    return
                try:
                    src = self._source(CDA)
                    title = src.api.describe(ref)
                    entry = self._add(src, ref, title, "")
                except (ProviderError, OSError) as e:
                    self._json(502, {"error": str(e)})
                    return
                scanned = collection.trigger_scan()
                self._json(200, {"title": entry["title"], "files": entry["files"], "scanned": scanned})
                return

            if u.path == "/api/remove":
                key = (body.get("key") or "").strip()
                with st.lock:
                    ok = collection.remove_series(st.data, key)
                    collection.save(st.path, st.data)
                scanned = collection.trigger_scan() if ok else False
                self._json(200 if ok else 404, {"removed": ok, "scanned": scanned})
                return

            if u.path == "/api/scan":
                self._json(200, {"scanned": collection.trigger_scan()})
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
        print(f"uwaga: ogladajanime nieaktywne ({e}) — zostaje CDA", flush=True)

    st = State(sources, collection.load(path), path, root, args.base_url)
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), build_handler(st))
    except OSError as e:
        print(
            f"nie mogę zająć portu {args.host}:{args.port} ({e}). "
            f"Sprawdź, czy panel już nie działa: ss -lntp | grep {args.port}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    print(f"panel: http://{args.host}:{args.port}/  (biblioteka: {root})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nzatrzymano", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
