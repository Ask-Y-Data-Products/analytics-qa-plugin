"""Create a local sign-off page beside, never inside, a sealed evidence case.

The page is written for the person who signs, not for an engineer, and the first
reader is often a stakeholder rather than an analyst. It opens with one plain
sentence - how many parts of the report were checked and how they came out - and
then the counts. Per component it leads with a status chip in three words anyone
knows (Looks right, Needs your decision, Problem found), says in one plain
sentence what the figure is, and then puts the questions first: each in its own
bordered block with a one-line reason and, where a claim can answer it, the two
buttons that do. Under the questions the captures tell the story situation by
situation - a grid of tiles, each headed by the situation's own title, the
filters that produced it, its key figures and the screenshot itself (click to
open it full size) - then the table of what the screen showed, then the checks an
analyst would make by hand (does the reset land back on the baseline, did the
change move anything, did it move the right way, does the card equal the table or
the ratio of its two inputs, did our own calculation agree), with the checks that
passed collapsed behind one line so a failure is never buried in green ticks. A
page-level card compares figures that carry the same name on more than one
component; a regression case leads with one sentence a manager can act on and
badges every component with what happened to it.

Every technical form - the DAX definition, the per-claim expectations, the
manifest digest and the generator identity - is kept in a collapsed block, and a
`@media print` block opens them all again for a reader who prints to PDF. Sign /
Reject buttons map to per-claim decisions (accepted, confirmed_defect,
unresolved) so the export stays compatible with `qa.py review`. Nothing is
pre-selected and the export stays disabled until the reviewer identifies
themselves and confirms.

The page works alone: the button downloads the decisions file. Served by
`review_server.py`, which announces its endpoint before this page's own script
runs, the same button reads "Submit decisions", posts them and shows the counts
and a link to the capture report the server just produced.

This module renders; `analyst_view.py` derives. Everything the page asserts about
the captures comes from the sealed observations, the run journal and the plan,
never from the agent's prose.
"""
import argparse
import base64
import html
import json
import os
import re
from pathlib import Path
from jinja2 import Template
from markupsafe import Markup

import analyst_view
from connections import digest
from qa import verify

STYLE = '''*{box-sizing:border-box}body{margin:0;color:#1f2528;background:#f3f5f6;font:16.5px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1400px;margin:auto;padding:20px 24px 60px}h1{font-size:30px;margin:6px 0 10px;line-height:1.2}h2{font-size:23px;margin:0}
.plain{max-width:72ch}
.headline{font-size:21px;line-height:1.35;margin:0 0 10px;color:#1f2528;font-weight:600}
.lead{color:#333b40;font-size:19px;line-height:1.4;margin:0 0 16px}.notice{border-left:4px solid #c27c11;padding:10px 14px;background:#fff6df;border-radius:4px;margin:0 0 18px}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}.pill{padding:6px 12px;border-radius:999px;background:#fff;border:1px solid #d3d9dc;font-size:14px}
.pill b{font-size:16px}.pill.pass b{color:#16794a}.pill.fail b{color:#b42318}.pill.open b{color:#8a5a00}
.changes{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 16px;font-size:14px}
.chg{padding:5px 11px;border-radius:999px;background:#fff;border:1px solid #d3d9dc;font-size:13.5px}
.chg.c-new-regression{border-color:#b42318;color:#b42318;font-weight:600}.chg.c-defect-fixed{border-color:#16794a;color:#16794a}
.chg.c-expected-change-pending-review{border-color:#c27c11;color:#8a5a00}.chg.c-still-open{border-color:#c27c11;color:#8a5a00}
.toc{background:#fff;border:1px solid #d9dfe2;border-radius:10px;padding:12px 16px;margin:0 0 20px}
.toc b{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:#586066}
.toc ol{list-style:none;display:flex;flex-wrap:wrap;gap:8px 18px;margin:8px 0 0;padding:0}
.toc li{display:flex;align-items:baseline;gap:6px;font-size:14px}.toc a{color:#0b5c8e;text-decoration:none;font-weight:600}
.toc a:hover{text-decoration:underline}.tag{font-size:12px;color:#586066;background:#f3f5f6;border-radius:999px;padding:2px 8px}
.tag.open{color:#8a5a00;background:#fff6df}
.card{background:#fff;border:1px solid #d9dfe2;border-radius:10px;padding:18px 20px;margin-bottom:22px;box-shadow:0 1px 2px rgba(0,0,0,.04);scroll-margin-top:12px}
.card.signed{border-color:#16794a;box-shadow:0 0 0 3px rgba(22,121,74,.15)}.card.rejected{border-color:#b42318;box-shadow:0 0 0 3px rgba(180,35,24,.15)}
.head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}.page{color:#586066;font-size:14px}
.definition{margin:8px 0 12px;color:#333b40}
h3{font-size:17.5px;margin:20px 0 8px;padding-bottom:5px;border-bottom:1px solid #e9eded;letter-spacing:0}
.shows{margin:6px 0 10px;color:#1f2528;font-size:16px}
details.tech{margin:0 0 10px}details.tech summary,details.note summary{cursor:pointer;color:#586066;font-size:13px}
details.tech p{margin:6px 0 0;color:#333b40;font-size:13.5px;overflow-wrap:anywhere}
.sits{display:grid;grid-template-columns:1fr;gap:11px;margin:10px 0 4px}
@media(min-width:1100px){.sits{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(min-width:1500px){.sits{grid-template-columns:repeat(3,minmax(0,1fr))}}
.sit{margin:0;padding:10px 12px 12px;border:1px solid #d9dfe2;border-radius:8px;background:#fafbfb;scroll-margin-top:12px}
.sit h3.sit-title{font-size:19px;margin:0 0 4px;padding:0;border:0;line-height:1.25}
.sit .ctx{color:#586066;font-size:13px}.sit .figs{font-size:13.5px;margin:2px 0 8px}.sit .figs b{font-variant-numeric:tabular-nums}
.sit .note{color:#586066;font-size:12.5px;margin:0 0 8px}
.sit .frame{display:block;border:1px solid #e3e7e9;border-radius:6px;overflow:hidden;background:#fff}
.sit img{display:block;width:100%;height:auto}
.sit.linked{border-color:#0b5c8e;box-shadow:0 0 0 3px rgba(11,92,142,.18)}
dialog.lightbox{border:0;border-radius:10px;padding:0;background:#fff;max-width:96vw;max-height:96vh;overflow:auto}
dialog.lightbox::backdrop{background:rgba(15,22,26,.72)}dialog.lightbox figure{margin:0}
dialog.lightbox img{display:block;max-width:95vw;max-height:85vh;width:auto;height:auto}
dialog.lightbox figcaption{padding:10px 14px;font-size:14px;color:#1f2528}
dialog.lightbox .hint{display:block;color:#586066;font-size:12.5px;margin:0;padding:0 14px 12px}
a.icon{text-decoration:none;margin-left:6px;font-size:13px}.views{margin-left:6px;font-size:12.5px}
.views a{color:#0b5c8e;text-decoration:none;border-bottom:1px dotted #9dc0d6;margin-left:6px}
.scroll{overflow-x:auto;border:1px solid #e3e7e9;border-radius:8px}
table.observed,table.sidebyside{width:100%;min-width:640px;margin:0;font-size:13.5px}
table.observed th,table.sidebyside th{background:#f7f9f9;font-size:12px;text-transform:uppercase;letter-spacing:.03em;color:#586066;white-space:nowrap}
table.observed td,table.observed th,table.sidebyside td,table.sidebyside th{padding:8px 10px;border-bottom:1px solid #e3e7e9}
table.observed tr:last-child td,table.sidebyside tr:last-child td{border-bottom:0}
table.observed td:first-child,table.sidebyside td:first-child{width:auto;font-weight:600}
table.observed .num,table.sidebyside .num{text-align:right;white-space:nowrap}
table.observed .delta{display:block;font-size:11.5px;color:#586066;font-weight:400}
table.observed tr.base{background:#f7f9f9}table.observed tr:hover,table.sidebyside tr:hover{background:#f2f7fa}
table.observed td a,table.sidebyside td a{color:#0b5c8e;text-decoration:none}table.observed td a:hover{text-decoration:underline}
.ok{color:#16794a;font-weight:600}.bad{color:#b42318;font-weight:600}
ul.checks{margin:0;padding:0;list-style:none}ul.checks li{padding:6px 0 6px 26px;position:relative;font-size:14px;border-bottom:1px solid #f0f3f4}
ul.checks li:last-child{border-bottom:0}ul.checks .mark{position:absolute;left:0;top:6px;font-weight:700}
ul.checks .pass .mark{color:#16794a}ul.checks .fail .mark{color:#b42318}ul.checks .unknown .mark{color:#8a5a00}
ul.checks li.fail{background:#fdecea;border-radius:6px;padding-left:26px;padding-right:10px;border-bottom:0;margin:4px 0}
ol.asks{margin:0;padding-left:20px}ol.asks li{margin:8px 0;font-size:14.5px}ol.asks li.issue{color:#1f2528}
ol.asks .detail{display:block;color:#586066;font-size:13.5px;margin-top:2px}
ol.asks .meta{display:inline-block;color:#8b959a;font-size:11.5px;margin-left:6px}
ol.asks details.note{margin-top:4px}ol.asks details.note p{margin:4px 0 0;font-size:12.5px;color:#586066;overflow-wrap:anywhere}
ol.asks li.issue::marker{color:#b42318;font-weight:700}ol.asks li.open::marker{color:#c27c11;font-weight:700}
.decide{display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap;margin-top:14px;padding-top:14px;border-top:1px solid #e3e7e9}
.decide button{font:inherit;font-weight:600;padding:10px 20px;border:2px solid transparent;border-radius:8px;cursor:pointer;min-width:120px}
.sign{background:#16794a;color:#fff}.reject{background:#fff;color:#b42318;border-color:#b42318!important}
button[aria-pressed=true].sign{box-shadow:0 0 0 3px rgba(22,121,74,.3)}button[aria-pressed=true].reject{background:#b42318;color:#fff}
button.clear{background:none;color:#586066;border-color:transparent;text-decoration:underline;min-width:0;padding:10px 6px}
.decide textarea{flex:1 1 320px;min-height:44px;font:inherit;border:1px solid #9aa4a9;border-radius:6px;padding:8px}.state{width:100%;font-size:13.5px;color:#586066;min-height:18px}
details.claims{margin-top:10px}details.claims summary{cursor:pointer;color:#586066;font-size:13.5px}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:8px}td,th{padding:8px 6px;border-bottom:1px solid #e3e7e9;vertical-align:top;text-align:left;overflow-wrap:anywhere}
details.claims td:first-child{width:74px;font-weight:600}select,textarea.small,input[type=text]{font:inherit;border:1px solid #9aa4a9;border-radius:6px;padding:6px;background:#fff;width:100%}
.status{font-size:12px;text-transform:uppercase;letter-spacing:.04em}.status.passed{color:#16794a}.status.failed{color:#b42318}.status.inconclusive{color:#8a5a00}
.badge{display:inline-block;font-size:11px;letter-spacing:.02em;border-radius:999px;padding:1px 7px;margin-top:3px;border:1px solid #d3d9dc;color:#586066}
.badge.c-new-regression{border-color:#b42318;color:#b42318}.badge.c-defect-fixed{border-color:#16794a;color:#16794a}
.hint{color:#586066;font-size:13.5px;margin:4px 0 8px}
.identity{background:#fff;border:1px solid #d9dfe2;border-radius:10px;padding:18px 20px;max-width:640px}.identity label{display:block;margin:8px 0}
#export{font:inherit;font-weight:600;padding:12px 22px;border:0;border-radius:8px;background:#0b5c8e;color:#fff;cursor:pointer}#export:disabled{opacity:.45;cursor:default}
.manifest{overflow-wrap:anywhere;font:12px ui-monospace,monospace;color:#586066}
.chips{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.chip{display:inline-block;font-size:14px;font-weight:600;border-radius:999px;padding:5px 14px;border:1px solid #d3d9dc;white-space:nowrap}
.chip.ok{color:#16794a;background:#eaf6f0;border-color:#a7d6c0}
.chip.decision{color:#8a5a00;background:#fff6df;border-color:#e6c98a}
.chip.problem{color:#b42318;background:#fdecea;border-color:#efb3ad}
.tag.ok{color:#16794a;background:#eaf6f0}.tag.problem{color:#b42318;background:#fdecea}.tag.decision{color:#8a5a00;background:#fff6df}
.asks{margin:0 0 4px}
.ask{border:1px solid #d9dfe2;border-left:5px solid #9aa4a9;border-radius:8px;padding:13px 16px;margin:0 0 12px;background:#fff}
.ask.issue{border-left-color:#b42318;background:#fffaf9}.ask.open{border-left-color:#c27c11;background:#fffdf7}
.ask.ok{border-left-color:#16794a}
.ask .q{margin:0;font-size:17.5px;line-height:1.4;font-weight:600;max-width:72ch}
.ask .why{margin:6px 0 0;color:#586066;font-size:14px;max-width:72ch}
.ask .detail{display:block;margin:6px 0 0;color:#333b40;font-size:15px;max-width:72ch}
.ask .meta{display:inline-block;color:#8b959a;font-size:12px;margin-left:6px}
.ask details.note{margin-top:6px}.ask details.note p{margin:4px 0 0;font-size:13px;color:#586066;overflow-wrap:anywhere}
.answer{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
.answer button{font:inherit;font-size:15px;font-weight:600;padding:9px 16px;border-radius:8px;border:2px solid #9aa4a9;background:#fff;color:#333b40;cursor:pointer}
.answer button.defect{border-color:#b42318;color:#b42318}.answer button.unclear{border-color:#c27c11;color:#8a5a00}
.answer button[aria-pressed=true].defect{background:#b42318;color:#fff}
.answer button[aria-pressed=true].unclear{background:#c27c11;color:#fff;border-color:#c27c11}
details.passed{margin:6px 0 0;border:1px solid #e3e7e9;border-radius:8px;padding:8px 12px;background:#fafbfb}
details.passed summary{cursor:pointer;color:#16794a;font-size:14.5px;font-weight:600}
details.passed ul.checks{margin-top:6px}
details.fineprint{margin:26px 0 0;border-top:1px solid #d9dfe2;padding-top:12px}
details.fineprint summary{cursor:pointer;color:#586066;font-size:14px}
details.fineprint dl{margin:10px 0 0;font-size:13.5px;color:#333b40}
details.fineprint dt{font-weight:600;color:#586066;margin-top:8px}details.fineprint dd{margin:2px 0 0;overflow-wrap:anywhere;font-family:ui-monospace,monospace;font-size:12.5px}
#submitted{margin:10px 0 0;padding:12px 14px;border:1px solid #16794a;border-left:5px solid #16794a;border-radius:8px;background:#eaf6f0;font-size:15.5px}
#submitted a{font-weight:700;color:#0b5c8e;font-size:16.5px}
#submitted.bad{border-color:#b42318;border-left-color:#b42318;background:#fdecea}
@media(max-width:760px){main{padding:12px}.decide button{flex:1 1 40%}}
@media print{
 body{background:#fff;font-size:12pt}main{max-width:none;padding:0}
 details{display:block}details>summary{color:#586066;font-weight:600}details>*{display:block!important}
 .decide,.answer,#export,.identity button,.identity label{display:none!important}
 .card,.ask,.sit,.toc{break-inside:avoid;page-break-inside:avoid}
 .sits{grid-template-columns:repeat(2,minmax(0,1fr))}
 a[href]{text-decoration:none;color:#1f2528}
 img{max-width:100%}
 *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}'''

SCRIPT = '''const p=JSON.parse(document.getElementById('payload').textContent);
const reviewer=document.getElementById('reviewer'),confirmation=document.getElementById('confirmation'),button=document.getElementById('export');
// review_server.py injects window.ANALYTICS_QA_REVIEW before this script when the page is served
// locally; opened from disk there is no endpoint and the page downloads the file exactly as before.
const server=(typeof window!=='undefined'&&window.ANALYTICS_QA_REVIEW)||null;
const online=!!(server&&server.endpoint);
const byStatus={sign:{passed:'accepted',failed:'confirmed_defect',inconclusive:'unresolved'},reject:{passed:'unresolved',failed:'unresolved',inconclusive:'unresolved'}};
function claimsOf(card){return Array.from(card.querySelectorAll('tr[data-claim]'));}
function update(){const any=Array.from(document.querySelectorAll('tbody select')).some(s=>s.value);button.disabled=!reviewer.value.trim()||!confirmation.checked||!any;}
function describe(card){const rows=claimsOf(card).filter(r=>r.querySelector('select').value);const n=rows.length;
 const el=card.querySelector('.state');if(!el)return;if(!n){el.textContent='No decision recorded for this component.';card.classList.remove('signed','rejected');return;}
 const kinds={};rows.forEach(r=>{const v=r.querySelector('select').value;kinds[v]=(kinds[v]||0)+1;});
 el.textContent=n+' of '+claimsOf(card).length+' expectations decided: '+Object.entries(kinds).map(([k,v])=>v+' '+k.replace('_',' ')).join(', ')+'.';}
function rowFor(id){return Array.from(document.querySelectorAll('tr[data-claim]')).find(r=>r.dataset.claim===id)||null;}
function syncAnswers(){document.querySelectorAll('.answer button[data-claim]').forEach(b=>{const row=rowFor(b.dataset.claim);
 const value=row?row.querySelector('select').value:'';b.setAttribute('aria-pressed',String(!!value&&value===b.dataset.decision));});}
function decide(card,mode){const map=byStatus[mode];card.querySelectorAll('.decide button[aria-pressed]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===mode)));
 card.classList.toggle('signed',mode==='sign');card.classList.toggle('rejected',mode==='reject');
 claimsOf(card).forEach(r=>{r.querySelector('select').value=map[r.dataset.status];});describe(card);syncAnswers();update();}
function clear(card){card.querySelectorAll('.decide button[aria-pressed]').forEach(b=>b.setAttribute('aria-pressed','false'));card.classList.remove('signed','rejected');
 claimsOf(card).forEach(r=>{r.querySelector('select').value='';});describe(card);syncAnswers();update();}
document.querySelectorAll('.answer button[data-claim]').forEach(b=>b.addEventListener('click',()=>{const row=rowFor(b.dataset.claim);if(!row)return;
 const select=row.querySelector('select');select.value=(select.value===b.dataset.decision)?'':b.dataset.decision;
 const card=row.closest('.card');
 if(card){card.querySelectorAll('.decide button[aria-pressed]').forEach(x=>x.setAttribute('aria-pressed','false'));card.classList.remove('signed','rejected');describe(card);}
 syncAnswers();update();}));
document.querySelectorAll('.card').forEach(card=>{const sign=card.querySelector('.sign');if(!sign)return;
 sign.addEventListener('click',()=>decide(card,'sign'));
 card.querySelector('.reject').addEventListener('click',()=>decide(card,'reject'));card.querySelector('.clear').addEventListener('click',()=>clear(card));
 card.querySelectorAll('tbody select').forEach(s=>s.addEventListener('change',()=>{card.querySelectorAll('.decide button[aria-pressed]').forEach(b=>b.setAttribute('aria-pressed','false'));describe(card);syncAnswers();update();}));});
const box=document.getElementById('lightbox'),boxImg=document.getElementById('lightbox-img'),boxCap=document.getElementById('lightbox-cap');
const modal=box&&typeof box.showModal==='function';
document.querySelectorAll('a.shot-link').forEach(a=>a.addEventListener('click',ev=>{
 if(!modal)return;ev.preventDefault();boxImg.src=a.getAttribute('href');boxImg.alt=a.dataset.caption||'';
 boxCap.textContent=a.dataset.caption||'';box.showModal();}));
if(box){box.addEventListener('click',()=>box.close());box.addEventListener('close',()=>boxImg.removeAttribute('src'));}
document.querySelectorAll('tr[data-sit]').forEach(row=>{const tile=document.getElementById(row.dataset.sit);if(!tile)return;
 row.addEventListener('mouseenter',()=>tile.classList.add('linked'));row.addEventListener('mouseleave',()=>tile.classList.remove('linked'));});
document.addEventListener('input',update);document.addEventListener('change',update);
function collect(){const at=new Date().toISOString();
 return Array.from(document.querySelectorAll('tr[data-claim]')).filter(r=>r.querySelector('select').value).map(r=>{const card=r.closest('.card');
 const own=r.querySelector('textarea').value.trim();const shared=card?card.querySelector('.decide textarea').value.trim():'';
 return {claim_id:r.dataset.claim,decision:r.querySelector('select').value,reviewer:reviewer.value.trim(),comment:own||shared,
 component_id:card?card.dataset.component:null,reviewed_manifest_sha256:p.manifest_sha256,
 confirmation:'Explicit reviewer export from local sign-off page; case '+p.case_id+'; manifest '+p.manifest_sha256+'; client time '+at};});}
function download(decisions){const blob=new Blob([JSON.stringify(decisions,null,2)],{type:'application/json'});
 const url=URL.createObjectURL(blob),a=document.createElement('a');
 a.href=url;a.download=p.case_id+'-decisions.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 document.getElementById('status').textContent='Decisions exported ('+decisions.length+'). Hand the file back to create the sealed review revision.';}
function tally(counts){return Object.keys(counts||{}).map(k=>counts[k]+' '+k.replace('_',' ')).join(', ');}
function present(data){const box=document.getElementById('submitted');box.hidden=false;box.className='';box.textContent='';
 const line=document.createElement('p');line.style.margin='0 0 8px';
 line.textContent='Recorded '+data.recorded+' decision'+(data.recorded===1?'':'s')+' for '+data.reviewer+': '+tally(data.counts)+'.';
 box.appendChild(line);
 if(data.report_url){const a=document.createElement('a');a.href=data.report_url;a.target='_blank';a.rel='noopener';
  a.textContent='Open the capture report this just produced';box.appendChild(a);}
 const note=document.createElement('p');note.style.margin='8px 0 0';note.style.fontSize='13.5px';note.style.color='#586066';
 note.textContent=(data.attestation||'')+' Sealed review revision: '+(data.revision||'');box.appendChild(note);}
function fail(message){const box=document.getElementById('submitted');box.hidden=false;box.className='bad';
 box.textContent='Not recorded. '+message;document.getElementById('status').textContent='';button.disabled=false;}
function submit(decisions){button.disabled=true;
 document.getElementById('status').textContent='Sending your decisions to the local review server...';
 fetch(server.endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(decisions)})
  .then(r=>r.json().then(d=>[r.ok,d],()=>[false,{errors:['The review server did not answer with JSON.']}]))
  .then(pair=>{const ok=pair[0],data=pair[1];
   if(!ok||!data.ok){throw new Error((data.errors||['The review server refused these decisions.']).join(' '));}
   document.getElementById('status').textContent='';present(data);})
  .catch(err=>fail(err.message));}
if(online){button.textContent='Submit decisions';
 const note=document.getElementById('submit-note');
 if(note)note.textContent='Submitting records these decisions on this machine and builds the capture report. This is a local attestation, not an authenticated signature.';}
button.addEventListener('click',()=>{if(button.disabled)return;const decisions=collect();
 if(!decisions.length){document.getElementById('status').textContent='No decision is recorded yet.';return;}
 if(online){submit(decisions);}else{download(decisions);}});'''

MARKS = {'pass': '✓', 'fail': '✗', 'unknown': '?'}
BLANK_CELL = '—'
DOT = ' · '
CAMERA = '📷'
LIGHTBOX = ('<dialog class="lightbox" id="lightbox" aria-label="Screenshot"><figure>'
            '<img id="lightbox-img" alt=""><figcaption id="lightbox-cap"></figcaption>'
            '<span class="hint">Click anywhere or press Escape to close.</span></figure></dialog>')


def plugin_version():
    """Version of the plugin this page was generated by; 'unknown' outside a plugin checkout."""
    try:
        return str(json.loads((Path(__file__).resolve().parent.parent / '.claude-plugin/plugin.json')
                              .read_text(encoding='utf-8-sig'))['version'])
    except (OSError, ValueError, KeyError, TypeError):
        return 'unknown'


def classification_class(value):
    return 'c-' + analyst_view.anchor_slug(str(value or '').replace(' ', '-')).lower()


def claim_row(claim):
    esc = html.escape
    options = '<option value="">Not reviewed</option>'
    if claim['status'] == 'passed':
        options += '<option value="accepted">Accept</option>'
    options += '<option value="confirmed_defect">Confirm defect</option><option value="unresolved">Needs clarification</option>'
    observed = esc(claim.get('observed', 'No observation recorded.'))
    classification = analyst_view.classification_of(claim)
    badge = (f'<br><span class="badge {classification_class(classification)}">{esc(classification)}</span>'
             if classification else '')
    return (f'<tr data-claim="{esc(claim["id"])}" data-status="{esc(claim["status"])}"><td>{esc(claim["id"])}<br>'
            f'<span class="status {esc(claim["status"])}">{esc(claim["status"])}</span>{badge}</td>'
            f'<td><b>{esc(claim["expected"])}</b><p>{observed}</p><small>{esc(claim["source"])}</small></td>'
            f'<td><select aria-label="Decision for {esc(claim["id"])}">{options}</select>'
            f'<textarea class="small" rows="2" placeholder="Comment for this expectation only"></textarea></td></tr>')


# --- situations ------------------------------------------------------------

def situation_index(observations, component_id, report_dir_rel):
    """Capture label -> everything the page needs to point at that situation.

    One place decides a situation's number, its anchor, its screenshot link and
    the caption the lightbox shows, so the tile, the observed table, a check and
    a question all open the same picture under the same name.
    """
    index = {}
    for position, observation in enumerate(observations):
        shots = observation.get('screenshots') or []
        source = report_dir_rel + '/' + shots[0] if shots else ''
        number = position + 1
        situation = str(observation.get('situation') or f'Situation {number}')
        index[observation['label']] = {
            'number': number, 'situation': situation,
            'anchor': analyst_view.situation_anchor(component_id, position), 'src': source,
            'caption': f'{number} · {situation} — {analyst_view.filter_phrase(observation)}'}
    return index


def shot_link(entry, body, klass='shot-link', title=None):
    """A link that opens the screenshot in the lightbox, or in a new tab without one."""
    esc = html.escape
    if not entry or not entry.get('src'):
        return ''
    label = title or f'Open the screenshot of {entry["situation"]}'
    return (f'<a class="{klass}" href="{esc(entry["src"])}" target="_blank" rel="noopener" '
            f'data-caption="{esc(entry["caption"])}" title="{esc(label)}" aria-label="{esc(label)}">{body}</a>')


def view_links(labels, index):
    """Small 'view' links to the screenshots of the situations one check concerns."""
    parts = []
    for label in dict.fromkeys(labels or []):
        entry = index.get(label)
        if not entry or not entry.get('src'):
            continue
        parts.append(shot_link(entry, f'view {entry["number"]}',
                               title=f'Open the screenshot of {entry["situation"]}'))
    return f'<span class="views">{"".join(parts)}</span>' if parts else ''


def situations_grid(observations, index):
    """One tile per captured situation: title, filters, figures, then the screenshot."""
    esc = html.escape
    tiles = []
    for observation in observations:
        entry = index[observation['label']]
        figures = DOT.join(f"{esc(f['name'])} <b>{esc(f['display'])}</b>"
                           for f in observation['figures'] if f['display'])
        note = observation.get('plan_description') or observation.get('description') or ''
        note = '' if analyst_view.is_technical(note) else note
        picture = ''
        if entry['src']:
            picture = shot_link(entry, f'<img src="{esc(entry["src"])}" alt="{esc(entry["situation"])}" '
                                       f'loading="lazy">', klass='shot-link frame')
        else:
            picture = '<p class="note">No screenshot was captured for this situation.</p>'
        tiles.append(
            f'<figure class="sit" id="{esc(entry["anchor"])}">'
            f'<h3 class="sit-title">{entry["number"]} · {esc(entry["situation"])}</h3>'
            f'<div class="ctx">{esc(analyst_view.filter_phrase(observation))}</div>'
            + (f'<div class="figs">{figures}</div>' if figures else '')
            + (f'<p class="note">{esc(note)}</p>' if note else '')
            + picture + '</figure>')
    return '<div class="sits">' + ''.join(tiles) + '</div>'


def observed_table(observations, index=None):
    """One row per captured situation, one column per figure, deltas against the baseline."""
    esc = html.escape
    index = index or {}
    if not observations:
        return '<p>No captures are attached to this component.</p>'
    baseline = observations[0]
    columns = []
    for observation in observations:
        for figure in observation['figures']:
            if figure['key'] not in [c['key'] for c in columns]:
                columns.append(figure)
    statuses = {o['label']: analyst_view.baseline_status(o, baseline) for o in observations}
    show_status = any(statuses.values())
    head = ''.join(f'<th class="num">{esc(c["name"])}</th>' for c in columns)
    head = f'<tr><th>Situation</th><th>Dates</th><th>Filters</th>{head}' + ('<th>Back to baseline?</th>' if show_status else '') + '</tr>'
    rows = []
    for position, observation in enumerate(observations):
        base = analyst_view.figures_of(baseline)
        cells = []
        for column in columns:
            figure = analyst_view.figures_of(observation).get(column['key'])
            if figure is None:
                cells.append(f'<td class="num">{BLANK_CELL}</td>')
                continue
            change = analyst_view.delta(figure, base.get(column['key'])) if position else None
            small = f'<span class="delta">{esc(change["text"])}</span>' if change else ''
            cells.append(f'<td class="num">{esc(figure["display"] or BLANK_CELL)}{small}</td>')
        status = statuses.get(observation['label'])
        cell = ''
        if show_status:
            if status:
                mark = MARKS['pass'] if status['status'] == 'pass' else MARKS['fail']
                klass = 'ok' if status['status'] == 'pass' else 'bad'
                cell = f'<td class="{klass}">{esc(status["text"])} {mark}</td>'
            else:
                cell = '<td></td>'
        entry = index.get(observation['label'])
        if entry:
            name = (f'<a href="#{esc(entry["anchor"])}">{entry["number"]} · {esc(observation["situation"])}</a>'
                    + shot_link(entry, CAMERA, klass='shot-link icon'))
            marker = f' data-sit="{esc(entry["anchor"])}"'
        else:
            name, marker = esc(observation['situation']), ''
        rows.append(f'<tr class="{"base" if position == 0 else ""}"{marker}><td>{name}</td>'
                    f'<td>{esc(observation["dates"])}</td>'
                    f'<td>{esc(analyst_view.changed_filters(observation, baseline if position else None))}</td>'
                    + ''.join(cells) + cell + '</tr>')
    return ('<div class="scroll"><table class="observed"><thead>' + head + '</thead><tbody>'
            + ''.join(rows) + '</tbody></table></div>')


def check_item(check, index):
    esc = html.escape
    links = check.get('links_html') or view_links(check.get('capture_labels'), index)
    return (f'<li class="{check["status"]}"><span class="mark">{MARKS.get(check["status"], "?")}</span>'
            f'{esc(check["text"])}{links}</li>')


def checks_list(checks, index=None, collapse_passed=True):
    """Failures and unsettled checks in the open; the ones that passed behind one line.

    A stakeholder needs to see what went wrong, not read eight green ticks to find
    it. The passed checks are not removed - the analyst opens the summary line.
    """
    index = index or {}
    if not checks:
        return '<p>Nothing could be checked automatically from these captures.</p>'
    hide = (lambda check: check['status'] == 'pass') if collapse_passed else (lambda check: False)
    passed = [c for c in checks if hide(c)]
    shown = [c for c in checks if not hide(c)]
    body = ''
    if shown:
        body += '<ul class="checks">' + ''.join(check_item(c, index) for c in shown) + '</ul>'
    if passed:
        body += (f'<details class="passed"><summary>{len(passed)} check'
                 f'{"" if len(passed) == 1 else "s"} passed</summary><ul class="checks">'
                 + ''.join(check_item(c, index) for c in passed) + '</ul></details>')
    return body


# The two decisions a question about an unsettled or failed expectation can carry. A passed
# expectation is never questioned, so 'accepted' is never offered here - only the analyst's
# own row in the expectations table can accept one, and nothing is pre-selected either way.
ANSWERS = [('confirmed_defect', 'defect', 'Yes, this is a problem'),
           ('unresolved', 'unclear', 'Not sure yet, needs clarification')]


def answer_buttons(claim_id):
    """The two buttons that answer one question, wired to that claim's own decision."""
    esc = html.escape
    if not claim_id:
        return ''
    buttons = ''.join(
        f'<button type="button" class="{klass}" data-claim="{esc(str(claim_id))}" '
        f'data-decision="{decision}" aria-pressed="false">{esc(label)}</button>'
        for decision, klass, label in ANSWERS)
    return f'<div class="answer">{buttons}</div>'


def question_block(ask, index=None):
    """One question in its own bordered block: the question, why we ask, then the answer."""
    esc = html.escape
    index = index or {}
    body = f'<p class="q">{esc(ask["text"])}</p>'
    if ask.get('why'):
        body += f'<p class="why">{esc(ask["why"])}</p>'
    if ask.get('detail'):
        body += f'<span class="detail">{esc(ask["detail"])}</span>'
    if ask.get('meta'):
        body += f'<span class="meta">{esc(ask["meta"])}</span>'
    body += ask.get('links_html') or view_links(ask.get('capture_labels'), index)
    if ask.get('technical_note'):
        body += ('<details class="note"><summary>Technical note</summary><p>'
                 + esc(ask['technical_note'][:600]) + '</p></details>')
    body += answer_buttons(ask.get('claim_id'))
    return f'<div class="ask {ask["severity"]}" data-question="{esc(str(ask.get("id") or ""))}">{body}</div>'


def questions_list(asks, index=None):
    return '<div class="asks">' + ''.join(question_block(ask, index) for ask in asks) + '</div>'


# --- cards -----------------------------------------------------------------

def component_view(component, claims, case_dir, catalog=None):
    """Everything one card needs, derived once: captures, checks, claims, questions."""
    observations = analyst_view.component_observations(component, case_dir)
    journal, plan = analyst_view.component_journal(component, case_dir)
    checks = analyst_view.automatic_checks(observations, plan, journal)
    own = [claims[c] for c in component.get('claim_ids', []) if c in claims]
    return {'component': component, 'observations': observations, 'checks': checks, 'claims': own,
            'asks': analyst_view.questions(component, own, checks, catalog)}


def status_chip(status):
    esc = html.escape
    return f'<span class="chip {status["key"]}">{esc(status["label"])}</span>'


def classification_badge(classification):
    """What happened to this component since the last approved version, on a regression case."""
    esc = html.escape
    if not classification:
        return ''
    return (f'<span class="badge {classification_class(classification)}" '
            f'title="Since the last approved version">{esc(classification)}</span>')


def component_card(view, report_dir_rel):
    esc = html.escape
    component, observations = view['component'], view['observations']
    index = situation_index(observations, component['id'], report_dir_rel)
    own, checks, asks = view['claims'], view['checks'], view['asks']
    passed = sum(1 for c in own if c['status'] == 'passed')
    rows = ''.join(claim_row(c) for c in own)
    definition = component.get('definition', '')
    technical = (f'<details class="tech"><summary>Technical definition</summary><p>{esc(definition)}</p></details>'
                 if definition else '')
    situations = (situations_grid(observations, index) if observations
                  else '<p>No screenshots captured for this component.</p>')
    chips = (status_chip(analyst_view.component_status(own))
             + classification_badge(analyst_view.component_classification(own)))
    return f'''<section class="card" id="{esc(analyst_view.component_anchor(component["id"]))}" data-component="{esc(component["id"])}">
<div class="head"><div><h2>{esc(component["name"])}</h2><div class="page">Report page: {esc(component.get("page", ""))} · {passed} of {len(own)} expectations passed</div></div><div class="chips">{chips}</div></div>
<h3>What this shows</h3><p class="shows plain">{esc(analyst_view.what_it_shows(component))}</p>
{technical}
<h3>Questions for you</h3>
{questions_list(asks, index)}
<h3>Situations</h3>
{situations}
<h3>What we observed</h3>
{observed_table(observations, index)}
<h3>Checks</h3>
{checks_list(checks, index)}
<div class="decide"><button type="button" class="sign" data-mode="sign" aria-pressed="false">Sign off</button><button type="button" class="reject" data-mode="reject" aria-pressed="false">Reject</button>
<textarea placeholder="Comment (applies to every expectation of this component unless overridden below)"></textarea><button type="button" class="clear">Clear</button>
<div class="state">No decision recorded for this component.</div></div>
<details class="claims"><summary>Every expectation, with the technical detail ({len(own)})</summary><table><thead><tr><th>ID</th><th>Expectation and observation</th><th>Decision</th></tr></thead><tbody>{rows}</tbody></table></details>
</section>'''


def cross_links(links, report_dir_rel):
    """'view' links for a page-level check, which already carries its own targets."""
    esc = html.escape
    parts = []
    for link in links or []:
        if not link.get('screenshot'):
            continue
        source = report_dir_rel + '/' + link['screenshot']
        parts.append(f'<a class="shot-link" href="{esc(source)}" target="_blank" rel="noopener" '
                     f'data-caption="{esc(link["label"])}" title="Open the screenshot of {esc(link["label"])}">'
                     f'view {esc(link["label"])}</a>')
    return f'<span class="views">{"".join(parts)}</span>' if parts else ''


def side_by_side(shared, report_dir_rel):
    """The same figure name under different filters, listed without a verdict."""
    esc = html.escape
    rows = []
    for entry in shared:
        for row in entry['rows']:
            situation = (f'<a href="#{esc(row["anchor"])}">{esc(row["situation"])}</a>'
                         if row.get('anchor') else esc(str(row['situation'])))
            if row.get('screenshot'):
                situation += (f'<a class="shot-link icon" href="{esc(report_dir_rel + "/" + row["screenshot"])}" '
                              f'target="_blank" rel="noopener" data-caption="{esc(row["component"])} — {esc(str(row["situation"]))}" '
                              f'title="Open the screenshot">{CAMERA}</a>')
            rows.append(f'<tr><td>{esc(entry["name"])}</td><td>{esc(row["component"])}</td><td>{situation}</td>'
                        f'<td>{esc(row["filters"])}</td><td class="num">{esc(row["display"] or BLANK_CELL)}</td></tr>')
    if not rows:
        return ''
    return ('<h3>Appears on several pages</h3>'
            '<p class="hint">The same name under different filters, so nothing is asserted here. '
            'Are these meant to differ?</p>'
            '<div class="scroll"><table class="sidebyside"><thead><tr><th>Figure</th><th>Component</th>'
            '<th>Situation</th><th>Filters</th><th class="num">Value</th></tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table></div>')


def across_card(views, report_dir_rel, catalog=None):
    """The page-level card: one figure name, more than one component."""
    result = analyst_view.cross_component_checks(views)
    checks, shared = result['checks'], result['shared']
    if not checks and not shared:
        return ''
    for check in checks:
        check['links_html'] = cross_links(check.get('links'), report_dir_rel)
    asks = analyst_view.questions({}, [], checks, catalog, fallback=False)
    for ask, check in zip(asks, [c for c in checks if c['status'] == 'fail']):
        ask['links_html'] = check.get('links_html', '')
    body = ''
    if checks:
        body += ('<h3>The same figure on more than one page</h3>'
                 '<p class="hint">Compared only where the dates and every slicer caption matched.</p>'
                 + checks_list(checks))
    body += side_by_side(shared, report_dir_rel)
    if asks:
        body += '<h3>Questions for you</h3>' + questions_list(asks)
    return ('<section class="card" id="across"><div class="head"><div><h2>Across components</h2>'
            '<div class="page">Figures that carry the same name on more than one component</div></div></div>'
            + body + '</section>')


def table_of_contents(views, has_across):
    """Component names with their pass counts and how many questions are still open."""
    esc = html.escape
    items = []
    for view in views:
        component, own = view['component'], view['claims']
        passed = sum(1 for c in own if c['status'] == 'passed')
        open_questions = sum(1 for a in view['asks'] if a.get('severity') != 'ok')
        status = analyst_view.component_status(own)
        tags = f'<span class="tag {status["key"]}">{esc(status["label"])}</span>'
        tags += f'<span class="tag">{passed}/{len(own)} passed</span>'
        if open_questions:
            tags += f'<span class="tag open">{open_questions} question{"s" if open_questions != 1 else ""}</span>'
        items.append(f'<li><a href="#{esc(analyst_view.component_anchor(component["id"]))}">'
                     f'{esc(component["name"])}</a>{tags}</li>')
    if has_across:
        items.append('<li><a href="#across">Across components</a></li>')
    if not items:
        return ''
    return '<nav class="toc" aria-label="Components"><b>Jump to</b><ol>' + ''.join(items) + '</ol></nav>'


def regression_strip(claims):
    """Counts per change_classification; nothing at all on a case that carries none."""
    esc = html.escape
    counts = analyst_view.classification_counts(claims)
    if not counts:
        return ''
    pills = ''.join(f'<span class="chg {classification_class(name)}"><b>{count}</b> {esc(name)}</span>'
                    for name, count in counts)
    return f'<div class="changes"><b>Since the reviewed baseline:</b>{pills}</div>'


PNG_LINK = re.compile(r'(src|href)="([^"]+\.png)"')


def embed_png_links(document, base_dir):
    """Replace relative PNG links with data URIs so the page works wherever it is opened.

    A sign-off page normally references the sealed case's screenshots relatively,
    which breaks when the file is mailed, previewed as a snapshot or moved away from
    the case folder. Embedding trades a bigger file for a self-contained one; the
    evidence itself stays in the sealed case.
    """
    base_dir = Path(base_dir)
    cache = {}

    def replace(match):
        attribute, link = match.group(1), html.unescape(match.group(2))
        if link.startswith('data:'):
            return match.group(0)
        if link not in cache:
            path = (base_dir / link).resolve()
            if not path.is_file():
                raise ValueError(f'Screenshot referenced by the page is missing: {link}')
            cache[link] = 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
        return f'{attribute}="{cache[link]}"'

    return PNG_LINK.sub(replace, document), len(cache)


def prepare(case_path, out_path, embed_images=False):
    case_dir, out = Path(case_path).resolve(), Path(out_path).resolve()
    if out.exists() or out.is_relative_to(case_dir):
        raise ValueError('Use a new output outside the sealed case')
    integrity = verify(case_dir)
    if not integrity.get('verified_files'):
        raise ValueError(f'Case must verify first: {integrity}')
    case = json.loads((case_dir / 'case.json').read_text(encoding='utf-8'))
    report_dir_rel = os.path.relpath(case_dir, out.parent).replace('\\', '/')
    report = report_dir_rel + '/report.html'
    manifest_sha = digest(case_dir / 'manifest.json')
    payload = json.dumps({'case_id': case['id'], 'manifest_sha256': manifest_sha,
                          'claims': case['claims']}).replace('<', '\\u003c')
    claims = {c['id']: c for c in case['claims']}
    components = case.get('components') or []
    covered = {cid for comp in components for cid in comp.get('claim_ids', [])}
    catalog = analyst_view.load_catalog()
    views = [component_view(comp, claims, case_dir, catalog) for comp in components]
    across = across_card(views, report_dir_rel, catalog)
    cards = [component_card(view, report_dir_rel) for view in views]
    leftover = [claims[c] for c in claims if c not in covered]
    if leftover:
        cards.append('<section class="card" data-component="other"><div class="head"><div><h2>Other expectations</h2>'
                     '<div class="page">Expectations that belong to no single component</div></div>'
                     '<div class="chips">' + status_chip(analyst_view.component_status(leftover))
                     + classification_badge(analyst_view.component_classification(leftover)) + '</div></div>'
                     '<div class="decide"><button type="button" class="sign" data-mode="sign" aria-pressed="false">Sign off</button><button type="button" class="reject" data-mode="reject" aria-pressed="false">Reject</button>'
                     '<textarea placeholder="Comment"></textarea><button type="button" class="clear">Clear</button><div class="state">No decision recorded for this component.</div></div>'
                     '<details class="claims" open><summary>Expectations (' + str(len(leftover)) + ')</summary><table><thead><tr><th>ID</th><th>Expectation and observation</th><th>Decision</th></tr></thead><tbody>'
                     + ''.join(claim_row(c) for c in leftover) + '</tbody></table></details></section>')
    counts = {s: sum(1 for c in case['claims'] if c['status'] == s) for s in ('passed', 'failed', 'inconclusive')}
    version = plugin_version()
    generator = html.escape(f'analytics-qa review_form {version}')
    case_id, sha = html.escape(str(case['id'])), html.escape(manifest_sha)
    header = table_of_contents(views, bool(across)) + across
    statuses = [analyst_view.component_status(view['claims']) for view in views]
    if leftover:
        statuses.append(analyst_view.component_status(leftover))
    summary = analyst_view.plain_summary(statuses, case['claims'])
    names = {cid: comp.get('name') for comp in components for cid in comp.get('claim_ids', [])}
    headline = analyst_view.regression_headline(case['claims'], names)
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="{generator}"><meta name="analytics-qa-case" content="{case_id}"><meta name="analytics-qa-manifest-sha256" content="{sha}">
<title>{{{{ title }}}}</title><style>{STYLE}</style><main data-generator="{generator}" data-case-id="{case_id}" data-manifest-sha256="{sha}">
<h1>{{{{ title }}}}</h1>
{{% if headline %}}<p class="headline plain">{{{{ headline }}}}</p>{{% endif %}}
<p class="lead plain">{{{{ summary }}}}</p>
<p class="notice">Awaiting your decisions. No business approval has been recorded.</p>
<div class="summary"><span class="pill pass"><b>{counts["passed"]}</b> passed</span><span class="pill fail"><b>{counts["failed"]}</b> defects found</span><span class="pill open"><b>{counts["inconclusive"]}</b> need a business decision</span><span class="pill"><b>{len(components)}</b> components</span></div>
{{{{ strip }}}}
{{{{ header }}}}
{{{{ cards }}}}
<section class="identity"><h2>Record decisions</h2><label>Reviewer<input id="reviewer" type="text" autocomplete="name"></label>
<label><input id="confirmation" type="checkbox"> I reviewed the screenshots and expectations I decided on.</label>
<button id="export" disabled>Export decisions</button><p id="status" role="status"></p>
<p id="submit-note" class="hint plain">Exporting downloads a decisions file; hand it back to create the sealed review revision. Your name is recorded exactly as you type it: this is a local attestation, not an authenticated signature.</p>
<div id="submitted" role="status" hidden></div></section>
<details class="fineprint"><summary>Technical identity of this review</summary>
<p class="hint plain">Per component this page shows what the figure is, what the screen showed in every situation we captured, the checks we ran on those numbers, and the questions only you can answer. <a href="{{{{ report }}}}" target="_blank">Full evidence report</a></p>
<dl><dt>Case</dt><dd>{case_id}</dd><dt>Evidence manifest (SHA-256)</dt><dd class="manifest">{{{{ manifest }}}}</dd>
<dt>Generated by</dt><dd>{generator}</dd></dl></details></main>
{LIGHTBOX}
<script type="application/json" id="payload">{{{{ payload }}}}</script><script>{SCRIPT}</script></html>'''
    rendered = Template(document, autoescape=True).render(
        title=str(case.get('target', 'Analytics evidence review')), report=report,
        manifest=manifest_sha, strip=Markup(regression_strip(case['claims'])), header=Markup(header),
        summary=summary, headline=headline,
        cards=Markup('\n'.join(cards)), payload=Markup(payload))
    embedded = 0
    if embed_images:
        rendered, embedded = embed_png_links(rendered, out.parent)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding='utf-8')
    print(json.dumps({'form': str(out), 'manifest_sha256': manifest_sha, 'components': len(components),
                      'generator': f'analytics-qa review_form {version}', 'embedded_images': embedded,
                      'state': 'awaiting actual user decisions'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--embed-images', action='store_true',
                        help='inline every screenshot as a data URI so the page renders when opened away from the case folder (mail, snapshot previews)')
    args = parser.parse_args()
    prepare(args.case, args.out, embed_images=args.embed_images)
