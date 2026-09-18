"""What the review found, for the person who has to decide what to fix.

The sealed evidence report holds everything; the sign-off page asks the analyst
to decide. Between them there is one more question nobody had a page for: what is
actually wrong with this report, worst first, and what was checked and came back
clean. This page answers it from `case.json` alone - every failed and
inconclusive claim, every `findings[]` record, and the detector claims with their
severity, hit counts and objects.

Nothing is recomputed and nothing is graded: a claim's own status decides where
it lands, the analyst-facing `question` is the heading (falling back to the
neutral phrasing `analyst_view.questions` already uses when the agent wrote for
engineers), and the evidence paths stay in a collapsed block. Given
`--signoff <page>`, every item links to that component's card there, so reading
this page and signing stay one click apart.

  collect(case)                    -> the records, ordered, each with its group
  severity_rank(record)            -> 0 for critical, 5 for unrated
  render(case_dir, out, signoff=None, title=None) -> writes the page, returns counts
"""
import argparse
import html
import json
from pathlib import Path

from jinja2 import Template
from markupsafe import Markup

import analyst_view
from analyst_view import component_anchor, first_sentence, is_technical, read_json

HERE = Path(__file__).resolve().parent

STYLE = '''*{box-sizing:border-box}body{margin:0;color:#1f2528;background:#f3f5f6;font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:auto;padding:20px 24px 60px}h1{font-size:26px;margin:6px 0 4px}h2{font-size:22px;margin:0}
.lead{color:#586066;margin:0 0 18px}.notice{border-left:4px solid #c27c11;padding:10px 14px;background:#fff6df;border-radius:4px;margin:0 0 18px}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}.pill{padding:6px 12px;border-radius:999px;background:#fff;border:1px solid #d3d9dc;font-size:14px}
.pill b{font-size:16px}.pill.pass b{color:#16794a}.pill.fail b{color:#b42318}.pill.open b{color:#8a5a00}
.toc{background:#fff;border:1px solid #d9dfe2;border-radius:10px;padding:12px 16px;margin:0 0 20px}
.toc b{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:#586066}
.toc ol{list-style:none;display:flex;flex-wrap:wrap;gap:8px 18px;margin:8px 0 0;padding:0}
.toc li{font-size:14px}.toc a{color:#0b5c8e;text-decoration:none;font-weight:600}.toc a:hover{text-decoration:underline}
.card{background:#fff;border:1px solid #d9dfe2;border-radius:10px;padding:18px 20px;margin-bottom:22px;box-shadow:0 1px 2px rgba(0,0,0,.04);scroll-margin-top:12px}
.head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}.page{color:#586066;font-size:14px}
h3{font-size:17.5px;margin:20px 0 8px;padding-bottom:5px;border-bottom:1px solid #e9eded}
h3:first-of-type{margin-top:6px}
.item{border:1px solid #e3e7e9;border-left:4px solid #d3d9dc;border-radius:8px;padding:12px 14px;margin:0 0 12px;background:#fafbfb;scroll-margin-top:12px}
.item.defect{border-left-color:#b42318;background:#fdf6f5}.item.open{border-left-color:#c27c11;background:#fffaf0}
.item.lint{border-left-color:#8a5a00}
.item h4{font-size:16px;margin:0 0 6px;line-height:1.35}
.item p{margin:4px 0;font-size:14px}.item .why{color:#333b40}
.item .impact{color:#1f2528}.item .impact b{font-weight:600}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 0;font-size:12px;color:#586066}
.meta span,.meta a{border:1px solid #d3d9dc;border-radius:999px;padding:2px 9px;background:#fff;text-decoration:none;color:#586066}
.meta a{color:#0b5c8e;border-color:#9dc0d6}.meta a:hover{text-decoration:underline}
.meta .sev-critical,.meta .sev-high{border-color:#b42318;color:#b42318}.meta .sev-medium{border-color:#c27c11;color:#8a5a00}
.objects{margin:6px 0 0;padding-left:20px;font-size:13.5px;color:#333b40}
.objects li{margin:2px 0}
.objects code,p code{font:12.5px ui-monospace,SFMono-Regular,Consolas,monospace;background:#f3f5f6;border-radius:4px;padding:1px 5px;overflow-wrap:anywhere}
details{margin:8px 0 0}details summary{cursor:pointer;color:#586066;font-size:13px}
details p,details li{margin:4px 0 0;color:#586066;font-size:12.5px;overflow-wrap:anywhere}
details ul{margin:6px 0 0;padding-left:20px}
.group{color:#586066;font-size:13.5px;margin:16px 0 6px;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:13.5px}
table th{background:#f7f9f9;font-size:12px;text-transform:uppercase;letter-spacing:.03em;color:#586066;text-align:left;white-space:nowrap}
table td,table th{padding:7px 10px;border-bottom:1px solid #e3e7e9;vertical-align:top;text-align:left;overflow-wrap:anywhere}
table tr:last-child td{border-bottom:0}table tr:hover{background:#f2f7fa}
.scroll{overflow-x:auto;border:1px solid #e3e7e9;border-radius:8px}
.clean{border-left:4px solid #16794a;background:#f2faf6;border-radius:6px;padding:12px 14px;font-size:15px}
.none{color:#586066;font-size:14px;margin:6px 0}
.manifest{overflow-wrap:anywhere;font:12px ui-monospace,monospace;color:#586066;margin-top:22px}
@media(max-width:760px){main{padding:12px}}'''

SEVERITY_ORDER = {'critical': 0, 'blocker': 0, 'high': 1, 'medium': 2, 'moderate': 2, 'low': 3,
                  'minor': 3, 'info': 4, 'informational': 4, 'review': 4}
UNRATED = 5
MAX_OBJECTS = 6
MAX_EVIDENCE = 12
NOTHING_FOUND = ('No defect and no open question is recorded in this case. Everything that was checked came '
                 'back as expected - which says the checks passed, not that the report is beyond question.')


def plugin_version():
    """Version of the plugin this page was generated by; 'unknown' outside a plugin checkout."""
    try:
        return str(json.loads((HERE.parent / '.claude-plugin/plugin.json').read_text(encoding='utf-8-sig'))['version'])
    except (OSError, ValueError, KeyError, TypeError):
        return 'unknown'


def strip_method_prefix(observed, method):
    """Drop the detector's own id from the start of an observation.

    `attach_detectors` prefixes a probe's observation with its method so the case
    record is unambiguous. On a page written for a reader the prefix is noise:
    "dax.assert: Observed 534 == expected 0" says nothing "Observed 534 ..."
    does not, and the method is already shown as a tag.
    """
    text = str(observed or '').strip()
    prefix = f'{method}: ' if method else ''
    if prefix and text.startswith(prefix):
        text = text[len(prefix):]
    return text


def severity_rank(record):
    """Where this record sorts: 0 is the most severe, 5 means nobody rated it."""
    value = str((record or {}).get('severity') or '').strip().lower()
    return SEVERITY_ORDER.get(value, UNRATED)


def detector_index(catalog=None):
    """Detector method id -> its catalog entry, including the ids a method is implemented as."""
    catalog = catalog if catalog is not None else analyst_view.load_catalog()
    index = {}
    for entry in (catalog or {}).get('detectors') or []:
        for key in (entry.get('id'), entry.get('implemented_as')):
            if key:
                index.setdefault(str(key), entry)
    return index


def plain_question(claim, catalog=None):
    """The heading for one claim: its own analyst question, or the neutral fallback.

    `analyst_view.questions` already owns this decision for the sign-off page -
    reuse it so both pages ask the same thing in the same words, and so a claim
    written for engineers lands in the technical note here too.
    """
    entries = analyst_view.questions({}, [claim], [], catalog, fallback=False)
    entry = next((e for e in entries if e.get('claim_id') == claim.get('id')), None)
    if entry:
        return entry['text'], entry.get('technical_note')
    text = str(claim.get('question') or '').strip()
    if text and not is_technical(text):
        return text, None
    return (first_sentence(claim.get('expected')) or f"Expectation {claim.get('id')}"), \
        str(claim.get('expected') or '') or None


def collect(case):
    """Every recorded problem and every passed check, ordered worst first.

    One record per claim, plus a record for any `findings[]` entry that no failed
    claim already carries (a finding about a failed claim is merged into it, so
    its title and business impact appear once, on the item they describe).
    `group` is one of defect, open, lint or passed.
    """
    case = case or {}
    catalog = analyst_view.load_catalog()
    index = detector_index(catalog)
    components = [c for c in case.get('components') or [] if isinstance(c, dict)]
    owner = {}
    for component in components:
        for claim_id in component.get('claim_ids') or []:
            owner.setdefault(claim_id, component)
    findings_by_claim = {}
    for finding in case.get('findings') or []:
        if not isinstance(finding, dict):
            continue
        for claim_id in finding.get('claim_ids') or []:
            findings_by_claim.setdefault(claim_id, []).append(finding)
    records, merged = [], set()
    for claim in case.get('claims') or []:
        if not isinstance(claim, dict):
            continue
        status = str(claim.get('status') or '')
        detector = str(claim.get('detector') or '')
        entry = index.get(detector) or {}
        component = owner.get(claim.get('id')) or {}
        question, note = plain_question(claim, catalog)
        related = findings_by_claim.get(claim.get('id')) or []
        if status == 'failed':
            group = 'defect'
        elif status == 'inconclusive':
            group = 'lint' if detector.startswith('lint.') else 'open'
        elif status == 'passed':
            group = 'passed'
        else:
            group = 'open'
        severity = claim.get('severity')
        if related and status == 'failed':
            severity = related[0].get('severity') or severity
            merged.update(id(f) for f in related)
        record = {
            'kind': 'claim', 'group': group, 'id': str(claim.get('id') or ''), 'title': question,
            'observed': strip_method_prefix(claim.get('observed'), claim.get('detector')),
            'expected': str(claim.get('expected') or ''),
            'technical_note': note, 'status': status,
            'impact': '; '.join(f.get('impact') for f in related if f.get('impact')) or None,
            'finding_titles': [f.get('title') for f in related if f.get('title')],
            'finding_ids': [f.get('id') for f in related if f.get('id')],
            'component': component.get('name'), 'component_id': component.get('id'),
            'page': component.get('page'), 'detector': detector or None,
            'detector_title': entry.get('title') or (analyst_view.detector_label(catalog, detector) if detector else None),
            'severity': severity, 'probe_status': claim.get('probe_status'),
            'hits': claim.get('hits'), 'objects': list(claim.get('objects') or []),
            'classification': analyst_view.classification_of(claim),
            'evidence': [str(e) for e in claim.get('evidence') or []]}
        records.append(record)
    for finding in case.get('findings') or []:
        if not isinstance(finding, dict) or id(finding) in merged:
            continue
        records.append({
            'kind': 'finding', 'group': 'defect', 'id': str(finding.get('id') or ''),
            'title': str(finding.get('title') or finding.get('id') or 'Finding'),
            'observed': str(finding.get('explanation') or ''), 'expected': '', 'technical_note': None,
            'status': 'failed', 'impact': finding.get('impact'), 'finding_titles': [], 'finding_ids': [],
            'component': None, 'component_id': None, 'page': None, 'detector': None,
            'detector_title': None, 'severity': finding.get('severity'), 'probe_status': None,
            'hits': None, 'objects': [], 'classification': None,
            'evidence': [str(e) for e in finding.get('evidence') or []],
            'claim_ids': [str(c) for c in finding.get('claim_ids') or []]})
    order = {'defect': 0, 'open': 1, 'lint': 2, 'passed': 3}
    records.sort(key=lambda r: (order[r['group']], severity_rank(r), str(r['component'] or '~'), r['id']))
    return records


# --- rendering --------------------------------------------------------------

def esc(value):
    return html.escape(str(value if value is not None else ''))


def component_link(record, signoff):
    """The component's name, linked to its card on the sign-off page when there is one."""
    name = record.get('component')
    if not name:
        return ''
    if signoff and record.get('component_id'):
        anchor = component_anchor(record['component_id'])
        return f'<a href="{esc(signoff)}#{esc(anchor)}">{esc(name)}</a>'
    return f'<span>{esc(name)}</span>'


def meta_row(record, signoff):
    parts = []
    link = component_link(record, signoff)
    if link:
        parts.append(link)
    if record.get('page') and record.get('page') != record.get('component'):
        parts.append(f'<span>page {esc(record["page"])}</span>')
    if record.get('severity'):
        parts.append(f'<span class="sev-{esc(str(record["severity"]).lower())}">'
                     f'severity {esc(record["severity"])}</span>')
    if record.get('detector_title'):
        parts.append(f'<span>{esc(record["detector_title"])}</span>')
    if record.get('probe_status'):
        parts.append(f'<span>probe {esc(record["probe_status"])}</span>')
    if record.get('classification'):
        parts.append(f'<span>{esc(record["classification"])}</span>')
    parts.append(f'<span>{esc(record["id"])}</span>')
    return f'<div class="meta">{"".join(parts)}</div>'


def evidence_block(record):
    paths = record.get('evidence') or []
    body = ''
    if record.get('technical_note'):
        body += f'<p>{esc(record["technical_note"][:800])}</p>'
    if paths:
        shown = paths[:MAX_EVIDENCE]
        items = ''.join(f'<li>{esc(path)}</li>' for path in shown)
        if len(paths) > len(shown):
            items += f'<li>... and {len(paths) - len(shown)} more.</li>'
        body += f'<ul>{items}</ul>'
    if not body:
        return ''
    return f'<details><summary>Evidence and technical wording</summary>{body}</details>'


def objects_block(record):
    objects = record.get('objects') or []
    if not objects:
        return ''
    shown = objects[:MAX_OBJECTS]
    items = ''.join(f'<li><code>{esc(obj)}</code></li>' for obj in shown)
    more = f'<li>... and {len(objects) - len(shown)} more.</li>' if len(objects) > len(shown) else ''
    return f'<ul class="objects">{items}{more}</ul>'


def same_sentence(left, right):
    """Two headings that say the same thing, up to the 'Defect:' prefix and punctuation."""
    def normalise(text):
        return ''.join(ch for ch in str(text or '').lower().removeprefix('defect: ') if ch.isalnum())
    first, second = normalise(left), normalise(right)
    return bool(first) and bool(second) and (first in second or second in first)


def item_block(record, signoff, klass):
    body = f'<h4>{esc(record["title"])}</h4>'
    titles = [t for t in record.get('finding_titles') or [] if not same_sentence(t, record['title'])]
    if titles:
        body += f'<p class="why"><b>{esc(titles[0])}</b></p>'
    observed = record.get('observed')
    if observed:
        body += f'<p class="why">{esc(observed[:900])}{"..." if len(observed) > 900 else ""}</p>'
    if record.get('impact'):
        body += f'<p class="impact"><b>Impact:</b> {esc(record["impact"])}</p>'
    body += objects_block(record)
    body += meta_row(record, signoff)
    body += evidence_block(record)
    return f'<div class="item {klass}" id="item-{esc(analyst_view.anchor_slug(record["id"]))}">{body}</div>'


def defects_card(records, signoff):
    items = [r for r in records if r['group'] == 'defect']
    if not items:
        body = '<p class="none">No claim failed and no finding was recorded.</p>'
    else:
        body = ''.join(item_block(r, signoff, 'defect') for r in items)
    return ('<section class="card" id="defects"><div class="head"><div><h2>Defects</h2>'
            f'<div class="page">{len(items)} recorded, most severe first</div></div></div>' + body + '</section>')


def open_card(records, signoff):
    items = [r for r in records if r['group'] == 'open']
    if not items:
        body = '<p class="none">Nothing was left unsettled.</p>'
    else:
        body = ''
        groups = {}
        for record in items:
            groups.setdefault(record['component'] or 'Not tied to one component', []).append(record)
        for name, group in groups.items():
            body += f'<p class="group">{esc(name)}</p>'
            body += ''.join(item_block(r, signoff, 'open') for r in group)
    return ('<section class="card" id="open"><div class="head"><div><h2>Open questions</h2>'
            f'<div class="page">{len(items)} check(s) nobody could settle automatically</div></div></div>'
            + body + '</section>')


def lint_card(records, signoff):
    items = [r for r in records if r['group'] == 'lint']
    if not items:
        return ''
    groups = {}
    for record in items:
        groups.setdefault(record.get('detector_title') or record.get('detector') or record['id'], []).append(record)
    body = ''
    for title, group in sorted(groups.items(), key=lambda pair: (severity_rank(pair[1][0]), pair[0])):
        hits = sum(int(r['hits'] or 0) for r in group) or sum(len(r['objects']) for r in group)
        body += f'<p class="group">{esc(title)} - {hits} object(s) flagged</p>'
        body += ''.join(item_block(r, signoff, 'lint') for r in group)
    return ('<section class="card" id="lint"><div class="head"><div><h2>Model lint</h2>'
            f'<div class="page">{len(groups)} check(s) flagged something in the model definition</div></div></div>'
            '<p class="none">These are patterns that often cause wrong numbers. Each needs a yes or a no: '
            'intended here, or a defect to fix.</p>' + body + '</section>')


def passed_card(records, signoff):
    items = [r for r in records if r['group'] == 'passed']
    if not items:
        body = '<p class="none">No check passed outright in this case.</p>'
    else:
        rows = ''.join(
            f'<tr><td>{esc(record["title"])}</td><td>{component_link(record, signoff) or "&mdash;"}</td>'
            f'<td>{esc(record.get("detector_title") or "observed on the report")}</td>'
            f'<td>{esc(record["id"])}</td></tr>' for record in items)
        body = ('<div class="scroll"><table><thead><tr><th>What was checked</th><th>Component</th>'
                '<th>How</th><th>ID</th></tr></thead><tbody>' + rows + '</tbody></table></div>')
    return ('<section class="card" id="passed"><div class="head"><div><h2>What was checked and passed</h2>'
            f'<div class="page">{len(items)} expectation(s) held</div></div></div>'
            '<p class="none">Here so the reader sees the scope of the review, not only its problems.</p>'
            + body + '</section>')


def table_of_contents(counts):
    items = [f'<li><a href="#defects">Defects ({counts["defects"]})</a></li>',
             f'<li><a href="#open">Open questions ({counts["open"]})</a></li>']
    if counts['lint']:
        items.append(f'<li><a href="#lint">Model lint ({counts["lint"]})</a></li>')
    items.append(f'<li><a href="#passed">Checked and passed ({counts["passed"]})</a></li>')
    return '<nav class="toc" aria-label="Contents"><b>Jump to</b><ol>' + ''.join(items) + '</ol></nav>'


def manifest_state(case_dir):
    """(digest, sealed) for the case, or ('unsealed', False) when it carries no manifest."""
    path = Path(case_dir) / 'manifest.json'
    if not path.is_file():
        return 'unsealed', False
    from connections import digest
    return digest(path), True


def render(case_dir, out, signoff=None, title=None):
    """Write the findings page beside the case and return the summary counts."""
    case_dir, out = Path(case_dir).resolve(), Path(out).resolve()
    if out.is_relative_to(case_dir):
        raise ValueError('Use an output outside the case; the case holds evidence, not derived pages')
    case = read_json(case_dir / 'case.json') or {}
    records = collect(case)
    counts = {'defects': sum(1 for r in records if r['group'] == 'defect'),
              'open': sum(1 for r in records if r['group'] == 'open'),
              'lint': sum(1 for r in records if r['group'] == 'lint'),
              'passed': sum(1 for r in records if r['group'] == 'passed')}
    components = len([c for c in case.get('components') or [] if isinstance(c, dict)])
    version = plugin_version()
    generator = f'analytics-qa findings_report {version}'
    sha, sealed = manifest_state(case_dir)
    heading = str(title or case.get('target') or case.get('id') or 'Analytics review')
    clean = not (counts['defects'] or counts['open'] or counts['lint'])
    banner = f'<p class="clean">{esc(NOTHING_FOUND)}</p>' if clean else ''
    signoff_note = (f'<p class="lead">Each item links to its component on the '
                    f'<a href="{esc(signoff)}">sign-off page</a>.</p>') if signoff else ''
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="{esc(generator)}"><meta name="analytics-qa-case" content="{esc(case.get('id', ''))}">
<meta name="analytics-qa-manifest-sha256" content="{esc(sha)}">
<title>{{{{ title }}}} - what we found</title><style>{STYLE}</style>
<main data-generator="{esc(generator)}" data-case-id="{esc(case.get('id', ''))}" data-manifest-sha256="{esc(sha)}">
<h1>{{{{ title }}}}: what we found</h1>
<p class="lead">Everything this review recorded as wrong or unsettled, worst first, and the checks that passed.
Read it to decide what to fix; the decisions themselves are recorded on the sign-off page.</p>
{{{{ signoff_note }}}}
<p class="notice">Findings, not verdicts: a defect is an expectation that did not hold in this evidence, and an
open question is one nobody could settle automatically. Neither is a business decision.</p>
<div class="summary"><span class="pill fail"><b>{counts['defects']}</b> defects</span>
<span class="pill open"><b>{counts['open']}</b> open questions</span>
<span class="pill open"><b>{counts['lint']}</b> model lint flags</span>
<span class="pill pass"><b>{counts['passed']}</b> passed</span>
<span class="pill"><b>{components}</b> components reviewed</span>
<span class="pill">{{{{ seal }}}}</span></div>
{{{{ banner }}}}
{{{{ toc }}}}
{{{{ defects }}}}
{{{{ open }}}}
{{{{ lint }}}}
{{{{ passed }}}}
<p class="manifest">Generated by {esc(generator)} from case {esc(case.get('id', ''))}; evidence manifest {{{{ manifest }}}}.</p>
</main></html>'''
    rendered = Template(document, autoescape=True).render(
        title=heading, seal='sealed evidence' if sealed else 'unsealed case', manifest=sha,
        banner=Markup(banner), signoff_note=Markup(signoff_note), toc=Markup(table_of_contents(counts)),
        defects=Markup(defects_card(records, signoff)), open=Markup(open_card(records, signoff)),
        lint=Markup(lint_card(records, signoff)), passed=Markup(passed_card(records, signoff)))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding='utf-8')
    return {'report': str(out), 'case': case.get('id'), 'defects': counts['defects'],
            'open_questions': counts['open'], 'lint_flags': counts['lint'], 'passed': counts['passed'],
            'components': components, 'signoff': signoff, 'manifest_sha256': sha, 'sealed': sealed,
            'generator': generator}


def main():
    parser = argparse.ArgumentParser(description='Build the findings report for a validation case.')
    parser.add_argument('--case', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--signoff', help='relative path or URL of the sign-off page, so every item links to '
                                          'its component card there')
    parser.add_argument('--title', help='heading for the page; the case target is used when omitted')
    args = parser.parse_args()
    print(json.dumps(render(args.case, args.out, args.signoff, args.title), default=str))


if __name__ == '__main__':
    main()
