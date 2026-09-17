"""Create a local sign-off page beside, never inside, a sealed evidence case.

The page is written for an analyst, not for an engineer. Per component it says
in one plain sentence what the figure is, shows the captured Power BI
screenshots in story order, then lays the captures out as a table of what the
screen actually showed in each situation, the checks an analyst would make by
hand (does the reset land back on the baseline, did the change move anything,
did it move the right way, does the card equal the table, did our own
calculation agree), and finally the questions the analyst has to answer. Every
technical form - the DAX definition, the per-claim expectations - is kept in a
collapsed block, and Sign / Reject buttons map to per-claim decisions
(accepted, confirmed_defect, unresolved) so the export stays compatible with
`qa.py review`. Nothing is pre-selected and the export stays disabled until the
reviewer identifies themselves and confirms.

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

STYLE = '''*{box-sizing:border-box}body{margin:0;color:#1f2528;background:#f3f5f6;font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1280px;margin:auto;padding:20px 24px 60px}h1{font-size:26px;margin:6px 0 4px}h2{font-size:20px;margin:0}
.lead{color:#586066;margin:0 0 18px}.notice{border-left:4px solid #c27c11;padding:10px 14px;background:#fff6df;border-radius:4px;margin:0 0 18px}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}.pill{padding:6px 12px;border-radius:999px;background:#fff;border:1px solid #d3d9dc;font-size:14px}
.pill b{font-size:16px}.pill.pass b{color:#16794a}.pill.fail b{color:#b42318}.pill.open b{color:#8a5a00}
.card{background:#fff;border:1px solid #d9dfe2;border-radius:10px;padding:18px 20px;margin-bottom:22px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.card.signed{border-color:#16794a;box-shadow:0 0 0 3px rgba(22,121,74,.15)}.card.rejected{border-color:#b42318;box-shadow:0 0 0 3px rgba(180,35,24,.15)}
.head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}.page{color:#586066;font-size:14px}
.definition{margin:8px 0 12px;color:#333b40}.strip{display:flex;gap:14px;overflow-x:auto;padding:6px 2px 10px;scroll-snap-type:x mandatory}
.shot{flex:0 0 380px;scroll-snap-align:start;border:1px solid #d9dfe2;border-radius:8px;background:#fafbfb;overflow:hidden}
.shot .crop{position:relative;height:214px;overflow:hidden;border-bottom:1px solid #e3e7e9;background:#fff}.shot img{position:absolute;display:block;width:128%;left:-3.7%;top:-38px}
.shot figcaption{padding:8px 10px 10px;font-size:13.5px}.shot .step{display:inline-block;font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:#586066;margin-bottom:3px}
.shot a{color:inherit;text-decoration:none}.defects{margin:10px 0 0;padding:0;list-style:none}.defects li{padding:8px 12px;border-radius:6px;margin:6px 0;font-size:14px}
.defects .failed{background:#fdecea;border-left:4px solid #b42318}.defects .inconclusive{background:#fff6df;border-left:4px solid #c27c11}
h3{font-size:15px;margin:18px 0 6px;letter-spacing:.02em}.shows{margin:6px 0 10px;color:#1f2528;font-size:16px}
details.tech{margin:0 0 10px}details.tech summary,details.note summary{cursor:pointer;color:#586066;font-size:13px}
details.tech p{margin:6px 0 0;color:#333b40;font-size:13.5px;overflow-wrap:anywhere}
.shot .figs{color:#1f2528;font-size:13px;margin-top:4px}.shot .note{color:#586066;font-size:12.5px;margin-top:4px}
.scroll{overflow-x:auto;border:1px solid #e3e7e9;border-radius:8px}
table.observed{width:100%;min-width:640px;margin:0;font-size:13.5px}table.observed th{background:#f7f9f9;font-size:12px;text-transform:uppercase;letter-spacing:.03em;color:#586066;white-space:nowrap}
table.observed td,table.observed th{padding:8px 10px;border-bottom:1px solid #e3e7e9}table.observed tr:last-child td{border-bottom:0}
table.observed td:first-child{width:auto;font-weight:600}table.observed .num{text-align:right;white-space:nowrap}
table.observed .delta{display:block;font-size:11.5px;color:#586066;font-weight:400}
table.observed tr.base{background:#f7f9f9}.ok{color:#16794a;font-weight:600}.bad{color:#b42318;font-weight:600}
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
td:first-child{width:64px;font-weight:600}select,textarea.small,input[type=text]{font:inherit;border:1px solid #9aa4a9;border-radius:6px;padding:6px;background:#fff;width:100%}
.status{font-size:12px;text-transform:uppercase;letter-spacing:.04em}.status.passed{color:#16794a}.status.failed{color:#b42318}.status.inconclusive{color:#8a5a00}
.identity{background:#fff;border:1px solid #d9dfe2;border-radius:10px;padding:18px 20px;max-width:640px}.identity label{display:block;margin:8px 0}
#export{font:inherit;font-weight:600;padding:12px 22px;border:0;border-radius:8px;background:#0b5c8e;color:#fff;cursor:pointer}#export:disabled{opacity:.45;cursor:default}
.manifest{overflow-wrap:anywhere;font:12px ui-monospace,monospace;color:#586066}
@media(max-width:760px){main{padding:12px}.shot{flex-basis:86vw}.decide button{flex:1 1 40%}}'''

SCRIPT = '''const p=JSON.parse(document.getElementById('payload').textContent);
const reviewer=document.getElementById('reviewer'),confirmation=document.getElementById('confirmation'),button=document.getElementById('export');
const byStatus={sign:{passed:'accepted',failed:'confirmed_defect',inconclusive:'unresolved'},reject:{passed:'unresolved',failed:'unresolved',inconclusive:'unresolved'}};
function claimsOf(card){return Array.from(card.querySelectorAll('tr[data-claim]'));}
function update(){const any=Array.from(document.querySelectorAll('tbody select')).some(s=>s.value);button.disabled=!reviewer.value.trim()||!confirmation.checked||!any;}
function describe(card){const rows=claimsOf(card).filter(r=>r.querySelector('select').value);const n=rows.length;
 const el=card.querySelector('.state');if(!n){el.textContent='No decision recorded for this component.';card.classList.remove('signed','rejected');return;}
 const kinds={};rows.forEach(r=>{const v=r.querySelector('select').value;kinds[v]=(kinds[v]||0)+1;});
 el.textContent=n+' of '+claimsOf(card).length+' expectations decided: '+Object.entries(kinds).map(([k,v])=>v+' '+k.replace('_',' ')).join(', ')+'.';}
function decide(card,mode){const map=byStatus[mode];card.querySelectorAll('.decide button[aria-pressed]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===mode)));
 card.classList.toggle('signed',mode==='sign');card.classList.toggle('rejected',mode==='reject');
 claimsOf(card).forEach(r=>{r.querySelector('select').value=map[r.dataset.status];});describe(card);update();}
function clear(card){card.querySelectorAll('.decide button[aria-pressed]').forEach(b=>b.setAttribute('aria-pressed','false'));card.classList.remove('signed','rejected');
 claimsOf(card).forEach(r=>{r.querySelector('select').value='';});describe(card);update();}
document.querySelectorAll('.card').forEach(card=>{card.querySelector('.sign').addEventListener('click',()=>decide(card,'sign'));
 card.querySelector('.reject').addEventListener('click',()=>decide(card,'reject'));card.querySelector('.clear').addEventListener('click',()=>clear(card));
 card.querySelectorAll('tbody select').forEach(s=>s.addEventListener('change',()=>{card.querySelectorAll('.decide button[aria-pressed]').forEach(b=>b.setAttribute('aria-pressed','false'));describe(card);update();}));});
document.addEventListener('input',update);document.addEventListener('change',update);
button.addEventListener('click',()=>{if(button.disabled)return;const at=new Date().toISOString();
 const decisions=Array.from(document.querySelectorAll('tr[data-claim]')).filter(r=>r.querySelector('select').value).map(r=>{const card=r.closest('.card');
 const own=r.querySelector('textarea').value.trim();const shared=card?card.querySelector('.decide textarea').value.trim():'';
 return {claim_id:r.dataset.claim,decision:r.querySelector('select').value,reviewer:reviewer.value.trim(),comment:own||shared,
 component_id:card?card.dataset.component:null,reviewed_manifest_sha256:p.manifest_sha256,
 confirmation:'Explicit reviewer export from local sign-off page; case '+p.case_id+'; manifest '+p.manifest_sha256+'; client time '+at};});
 const blob=new Blob([JSON.stringify(decisions,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');
 a.href=url;a.download=p.case_id+'-decisions.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 document.getElementById('status').textContent='Decisions exported ('+decisions.length+'). Hand the file back to create the sealed review revision.';});'''

MARKS = {'pass': '✓', 'fail': '✗', 'unknown': '?'}
BLANK_CELL = '—'
DOT = ' · '


def plugin_version():
    """Version of the plugin this page was generated by; 'unknown' outside a plugin checkout."""
    try:
        return str(json.loads((Path(__file__).resolve().parent.parent / '.claude-plugin/plugin.json')
                              .read_text(encoding='utf-8-sig'))['version'])
    except (OSError, ValueError, KeyError, TypeError):
        return 'unknown'


def claim_row(claim):
    esc = html.escape
    options = '<option value="">Not reviewed</option>'
    if claim['status'] == 'passed':
        options += '<option value="accepted">Accept</option>'
    options += '<option value="confirmed_defect">Confirm defect</option><option value="unresolved">Needs clarification</option>'
    observed = esc(claim.get('observed', 'No observation recorded.'))
    return (f'<tr data-claim="{esc(claim["id"])}" data-status="{esc(claim["status"])}"><td>{esc(claim["id"])}<br>'
            f'<span class="status {esc(claim["status"])}">{esc(claim["status"])}</span></td>'
            f'<td><b>{esc(claim["expected"])}</b><p>{observed}</p><small>{esc(claim["source"])}</small></td>'
            f'<td><select aria-label="Decision for {esc(claim["id"])}">{options}</select>'
            f'<textarea class="small" rows="2" placeholder="Comment for this expectation only"></textarea></td></tr>')


def screenshot_strip(observations, report_dir_rel):
    """Each captured situation: what was on screen, under which filters, with its figures."""
    esc = html.escape
    shots = []
    for index, observation in enumerate(observations):
        if not observation['screenshots']:
            continue
        src = report_dir_rel + '/' + observation['screenshots'][0]
        figures = DOT.join(f"{f['name']} {f['display']}" for f in observation['figures'] if f['display'])
        note = observation.get('plan_description') or observation.get('description') or ''
        note = '' if analyst_view.is_technical(note) else note
        caption = (f'<span class="step">Situation {index + 1}/{len(observations)}</span><br>'
                   f'<b>{esc(observation["situation"])}</b>: {esc(analyst_view.filter_phrase(observation))}')
        if figures:
            caption += f'<div class="figs">{esc(figures)}</div>'
        if note:
            caption += f'<div class="note">{esc(note)}</div>'
        shots.append(f'<figure class="shot"><a href="{esc(src)}" target="_blank" title="Open the full screenshot">'
                     f'<div class="crop"><img src="{esc(src)}" alt="{esc(observation["situation"])}" loading="lazy"></div></a>'
                     f'<figcaption>{caption}</figcaption></figure>')
    return ''.join(shots)


def observed_table(observations):
    """One row per captured situation, one column per figure, deltas against the baseline."""
    esc = html.escape
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
    for index, observation in enumerate(observations):
        base = analyst_view.figures_of(baseline)
        cells = []
        for column in columns:
            figure = analyst_view.figures_of(observation).get(column['key'])
            if figure is None:
                cells.append(f'<td class="num">{BLANK_CELL}</td>')
                continue
            change = analyst_view.delta(figure, base.get(column['key'])) if index else None
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
        rows.append(f'<tr class="{"base" if index == 0 else ""}"><td>{esc(observation["situation"])}</td>'
                    f'<td>{esc(observation["dates"])}</td>'
                    f'<td>{esc(analyst_view.changed_filters(observation, baseline if index else None))}</td>'
                    + ''.join(cells) + cell + '</tr>')
    return ('<div class="scroll"><table class="observed"><thead>' + head + '</thead><tbody>'
            + ''.join(rows) + '</tbody></table></div>')


def checks_list(checks):
    esc = html.escape
    if not checks:
        return '<p>Nothing could be checked automatically from these captures.</p>'
    items = [f'<li class="{c["status"]}"><span class="mark">{MARKS.get(c["status"], "?")}</span>{esc(c["text"])}</li>'
             for c in checks]
    return '<ul class="checks">' + ''.join(items) + '</ul>'


def questions_list(asks):
    esc = html.escape
    items = []
    for ask in asks:
        body = esc(ask['text'])
        if ask.get('detail'):
            body += f'<span class="detail">{esc(ask["detail"])}</span>'
        if ask.get('meta'):
            body += f'<span class="meta">{esc(ask["meta"])}</span>'
        if ask.get('technical_note'):
            body += ('<details class="note"><summary>Technical note</summary><p>'
                     + esc(ask['technical_note'][:600]) + '</p></details>')
        items.append(f'<li class="{ask["severity"]}">{body}</li>')
    return '<ol class="asks">' + ''.join(items) + '</ol>'


def component_card(component, claims, report_dir_rel, case_dir, catalog=None):
    esc = html.escape
    observations = analyst_view.component_observations(component, case_dir)
    journal, plan = analyst_view.component_journal(component, case_dir)
    checks = analyst_view.automatic_checks(observations, plan, journal)
    own = [claims[c] for c in component.get('claim_ids', []) if c in claims]
    asks = analyst_view.questions(component, own, checks, catalog)
    passed = sum(1 for c in own if c['status'] == 'passed')
    rows = ''.join(claim_row(c) for c in own)
    definition = component.get('definition', '')
    technical = (f'<details class="tech"><summary>Technical definition</summary><p>{esc(definition)}</p></details>'
                 if definition else '')
    return f'''<section class="card" data-component="{esc(component["id"])}">
<div class="head"><div><h2>{esc(component["name"])}</h2><div class="page">Report page: {esc(component.get("page", ""))} · {passed} of {len(own)} expectations passed</div></div></div>
<h3>What this shows</h3><p class="shows">{esc(analyst_view.what_it_shows(component))}</p>
{technical}
<div class="strip">{screenshot_strip(observations, report_dir_rel) or '<p>No screenshots captured for this component.</p>'}</div>
<h3>What we observed</h3>
{observed_table(observations)}
<h3>Checks</h3>
{checks_list(checks)}
<h3>Questions for you</h3>
{questions_list(asks)}
<div class="decide"><button type="button" class="sign" data-mode="sign" aria-pressed="false">Sign off</button><button type="button" class="reject" data-mode="reject" aria-pressed="false">Reject</button>
<textarea placeholder="Comment (applies to every expectation of this component unless overridden below)"></textarea><button type="button" class="clear">Clear</button>
<div class="state">No decision recorded for this component.</div></div>
<details class="claims"><summary>Every expectation, with the technical detail ({len(own)})</summary><table><thead><tr><th>ID</th><th>Expectation and observation</th><th>Decision</th></tr></thead><tbody>{rows}</tbody></table></details>
</section>'''


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
    cards = [component_card(comp, claims, report_dir_rel, case_dir, catalog) for comp in components]
    leftover = [claims[c] for c in claims if c not in covered]
    if leftover:
        cards.append('<section class="card" data-component="other"><div class="head"><h2>Other expectations</h2></div>'
                     '<div class="decide"><button type="button" class="sign" data-mode="sign" aria-pressed="false">Sign off</button><button type="button" class="reject" data-mode="reject" aria-pressed="false">Reject</button>'
                     '<textarea placeholder="Comment"></textarea><button type="button" class="clear">Clear</button><div class="state">No decision recorded for this component.</div></div>'
                     '<details class="claims" open><summary>Expectations (' + str(len(leftover)) + ')</summary><table><thead><tr><th>ID</th><th>Expectation and observation</th><th>Decision</th></tr></thead><tbody>'
                     + ''.join(claim_row(c) for c in leftover) + '</tbody></table></details></section>')
    counts = {s: sum(1 for c in case['claims'] if c['status'] == s) for s in ('passed', 'failed', 'inconclusive')}
    version = plugin_version()
    generator = html.escape(f'analytics-qa review_form {version}')
    case_id, sha = html.escape(str(case['id'])), html.escape(manifest_sha)
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="{generator}"><meta name="analytics-qa-case" content="{case_id}"><meta name="analytics-qa-manifest-sha256" content="{sha}">
<title>{{{{ title }}}}</title><style>{STYLE}</style><main data-generator="{generator}" data-case-id="{case_id}" data-manifest-sha256="{sha}">
<h1>{{{{ title }}}}</h1><p class="lead">For each component: what it shows, what the screen showed in every situation we captured, the checks we ran on those numbers, and the questions only you can answer. Then sign off or reject. <a href="{{{{ report }}}}" target="_blank">Full evidence report</a></p>
<p class="notice">Awaiting your decisions. No business approval has been recorded.</p>
<div class="summary"><span class="pill pass"><b>{counts["passed"]}</b> passed</span><span class="pill fail"><b>{counts["failed"]}</b> defects found</span><span class="pill open"><b>{counts["inconclusive"]}</b> need a business decision</span><span class="pill"><b>{len(components)}</b> components</span></div>
{{{{ cards }}}}
<section class="identity"><h2>Record decisions</h2><label>Reviewer<input id="reviewer" type="text" autocomplete="name"></label>
<label><input id="confirmation" type="checkbox"> I reviewed the screenshots and expectations I decided on.</label>
<button id="export" disabled>Export decisions</button><p id="status" role="status"></p>
<p class="manifest">Evidence manifest: {{{{ manifest }}}}</p></section></main>
<script type="application/json" id="payload">{{{{ payload }}}}</script><script>{SCRIPT}</script></html>'''
    rendered = Template(document, autoescape=True).render(
        title=str(case.get('target', 'Analytics evidence review')), report=report,
        manifest=manifest_sha, cards=Markup('\n'.join(cards)), payload=Markup(payload))
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
