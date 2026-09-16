"""Plan-driven baseline / change / reset cycle on the Power BI Desktop WebView.

The agent designs the experiment (which page, which control, which DAX oracle
decides what a correct screen shows); this runner performs it one observed step
at a time and refuses to continue after a failed receipt. Each capture writes
screen.png, dom.html, state.json and a state_checks receipt; oracles are executed
on the connected catalog and retained under evidence/dax.

Plan (JSON):
{
  "page": "Contract Velocity",
  "slicers": ["Franchise", "Channel", "Province", "Campus"],   # captions recorded
  "date_slicer": "Date",                                       # both inputs recorded; null if none
  "cards": ["Starts", "Median Days to Contract"],              # numeric value under each card title
  "tables": {"velocity": "channel_name"},                      # key -> text that identifies the table; Total row parsed
  "charts": ["Reinquiries and New Inquiries by Channel"],      # SVG marks, highlight state, selected marks
  "oracles": {"three": "EVALUATE ..."},                        # read-only DAX executed on the connection
  "steps": [
    {"capture": "state1_baseline", "description": "...",
     "expect": {"/cards/Starts": {"oracle": "three", "column": "[Starts]"},
                "/date_end": "7/13/2026", "/slicers/Channel": "Multiple selections"}},
    {"toggle_member": {"slicer": "Channel", "label": "Google Ads", "selected": false}},
    {"set_date": {"slicer": "Date", "end": "7/7/2026"}},
    {"click_mark": {"chart": "Reinquiries and New Inquiries by Channel", "category": "Referrals"}},
    {"select_option": {"slicer": "Include Other Expenses", "label": "All Expenses"}},   # list/tile slicer option
    {"go_to_page": "Meta Daily"}
  ]
}
Every capture also checks the report title, the active page and that no dropdown,
calendar or edit-mode overlay is open. Expectations may be literal JSON values or
{"oracle": name, "row": 0, "column": "[Starts]", "round": 0} references ("round" matches a
card's displayed precision). Each capture records `cards` (parsed numbers, null when the
card shows text such as "$240K"), `cards_text` (the raw text), `tables`, `slicers`
(dropdown caption or the selected options of a list slicer) and `charts`.

Usage: python pbi_cycle.py --connection connection.json --plan plan.json --out <fresh directory>
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connections import dump, ensure_evidence_writable  # noqa: E402
from pbi import dax  # noqa: E402
from state_checks import check_state  # noqa: E402
import powerbi_controls as pc  # noqa: E402

MARKS_JS = """e => Array.from(e.querySelectorAll('svg rect.bar, svg rect.column, svg path.slice, svg circle.dot')).map(m => ({
  cls: m.getAttribute('class'), value: m.getAttribute('aria-label'),
  x: Math.round(m.getBoundingClientRect().x), y: Math.round(m.getBoundingClientRect().y),
  w: Math.round(m.getBoundingClientRect().width), h: Math.round(m.getBoundingClientRect().height),
  opacity: getComputedStyle(m).fillOpacity}))"""
LABELS_JS = """e => Array.from(e.querySelectorAll('svg text.setFocusRing, svg g.tick text')).map(t => ({
  text: (t.querySelector('title') || {}).textContent || t.textContent,
  x: Math.round(t.getBoundingClientRect().x + t.getBoundingClientRect().width / 2),
  y: Math.round(t.getBoundingClientRect().y + t.getBoundingClientRect().height / 2)}))"""


def highlight_active(marks):
    """Power BI dims unselected marks (fill-opacity below 1) while a selection is active."""
    return any(float(m['opacity'] or 1) < 1 for m in marks)


def selected_marks(marks):
    if not highlight_active(marks):
        return 0
    return sum(1 for m in marks if float(m['opacity'] or 1) >= 1 and (m['w'] > 0 or m['h'] > 0)
               and 'highlight' not in (m['cls'] or '').split())


def chart_marks(page, title):
    visual = pc.find_visual(page, title)
    marks = visual.evaluate(MARKS_JS)
    return {'marks': marks, 'labels': visual.evaluate(LABELS_JS),
            'highlight_active': highlight_active(marks), 'selected_marks': selected_marks(marks),
            'values': [m['value'] for m in marks if 'highlight' not in (m['cls'] or '').split()]}


def find_mark(chart, category):
    rows = [l for l in chart['labels'] if l['text'].strip() == category]
    if len(rows) != 1:
        raise ValueError(f'Category label {category!r}: found {len(rows)} in the chart axis')
    label = rows[0]
    horizontal = [m for m in chart['marks'] if m['w'] > 0 and abs(m['y'] + m['h'] / 2 - label['y']) <= 6]
    vertical = [m for m in chart['marks'] if m['h'] > 0 and abs(m['x'] + m['w'] / 2 - label['x']) <= 6]
    candidates = horizontal or vertical
    if not candidates:
        raise ValueError(f'No mark aligned with {category!r}')
    return max(candidates, key=lambda m: m['w'] * m['h'])


def resolve(expected, oracles):
    resolved = {}
    for pointer, wanted in expected.items():
        if isinstance(wanted, dict) and 'oracle' in wanted:
            rows = oracles[wanted['oracle']]['rows']
            value = rows[wanted.get('row', 0)][wanted['column']]
            if 'round' in wanted and isinstance(value, (int, float)):
                digits = int(wanted['round'])
                value = int(round(value + 1e-9)) if digits == 0 else round(value, digits)
            resolved[pointer] = value
        else:
            resolved[pointer] = wanted
    return resolved


def derive(state, plan):
    derived = {'cards': {}, 'cards_text': {}, 'tables': {}}
    for title in plan.get('cards', []):
        text = pc.visual_text(state, title)
        derived['cards_text'][title] = text
        try:
            derived['cards'][title] = pc.parse_number(text)
        except ValueError:
            derived['cards'][title] = None
    for key, marker in (plan.get('tables') or {}).items():
        try:
            derived['tables'][key] = pc.table_total(state, marker)
        except (KeyError, ValueError) as exc:
            derived['tables'][key] = None
            derived.setdefault('table_errors', {})[key] = str(exc)
    return derived


def run(connection, plan, out):
    out = Path(out).resolve()
    ensure_evidence_writable(out)
    if (out / 'evidence').exists():
        raise ValueError('Output root already holds evidence; choose a fresh directory')
    conn = json.loads(Path(connection).read_text(encoding='utf-8-sig'))
    plan = json.loads(Path(plan).read_text(encoding='utf-8-sig'))
    plan['page'] = plan['page'].strip()
    title = conn.get('report_title') or Path(conn['report']).stem
    oracles = {}
    for name, query in (plan.get('oracles') or {}).items():
        oracles[name] = dax(conn['port'], query, conn['database'])
        dump(out / 'evidence/dax' / f'{name}.json', oracles[name])
    journal = {'kind': 'pbi_cycle', 'started_at': dt.datetime.now(dt.timezone.utc).isoformat(),
               'connection': {k: conn.get(k) for k in ('port', 'database', 'browser_endpoint', 'page_id', 'report')},
               'report_title': title, 'page': plan['page'], 'plan': plan, 'states': [], 'actions': [], 'status': 'running'}
    dump(out / 'evidence/journal.json', journal)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser, page = pc.connect_page(p, conn['browser_endpoint'], conn['page_id'], title)
        try:
            pages = pc.report_pages(page)
            pc.go_to_page(page, plan['page'], pages)
            pc.dismiss_edit_overlays(page)
            for step in plan['steps']:
                if 'capture' in step:
                    label = step['capture']
                    folder = out / 'evidence' / label
                    extra = {'charts': {t: chart_marks(page, t) for t in plan.get('charts', [])}}
                    state = pc.observe(page, folder, title, label, slicer_titles=plan.get('slicers', []),
                                       date_slicer_title=plan.get('date_slicer', 'Date'), known_pages=pages, extra=extra)
                    state.update(derive(state, plan))
                    state['description'] = step.get('description', '')
                    state['derived'] = 'cards/tables parsed from this observation\'s own visual text after capture'
                    dump(folder / 'state.json', state)
                    expected = {'/verified_report_title': title, '/active_page': plan['page'],
                                '/popup_open': False, '/calendar_open': False, '/edit_overlay_open': False}
                    expected.update(resolve(step.get('expect', {}), oracles))
                    record = {'label': label, 'description': step.get('description', ''), 'cards': state['cards'],
                              'tables': state['tables'], 'date': [state['date_start'], state['date_end']], 'slicers': state['slicers']}
                    try:
                        receipt = check_state(folder / 'state.json', expected, folder / 'check.json')
                        record['receipt'] = receipt['status']
                    except AssertionError:
                        record['receipt'] = 'failed'
                        journal['states'].append(record)
                        journal['status'] = 'stopped_on_failed_receipt'
                        raise
                    journal['states'].append(record)
                elif 'toggle_member' in step:
                    s = step['toggle_member']
                    result = pc.toggle_member(page, pc.find_visual(page, s['slicer']), s['label'], s['selected'], s.get('allow_other_changes', False))
                    journal['actions'].append({'step': step, 'observed': result})
                elif 'set_date' in step:
                    s = step['set_date']
                    result = pc.set_date_range(page, pc.find_visual(page, s.get('slicer', plan.get('date_slicer', 'Date'))), s.get('start'), s.get('end'))
                    journal['actions'].append({'step': step, 'observed': result})
                elif 'click_mark' in step:
                    s = step['click_mark']
                    pc.dismiss_edit_overlays(page)
                    chart = chart_marks(page, s['chart'])
                    mark = find_mark(chart, s['category'])
                    page.mouse.click(mark['x'] + mark['w'] / 2, mark['y'] + mark['h'] / 2)
                    page.wait_for_timeout(1200)
                    pc.wait_stable(page)
                    after = chart_marks(page, s['chart'])
                    journal['actions'].append({'step': step, 'observed': {'clicked_mark': mark, 'highlight_active_after': after['highlight_active'],
                                                                           'selected_marks_after': after['selected_marks']}})
                elif 'select_option' in step:
                    s = step['select_option']
                    result = pc.select_option(page, pc.find_visual(page, s['slicer']), s['label'])
                    journal['actions'].append({'step': step, 'observed': result})
                elif 'go_to_page' in step:
                    pc.go_to_page(page, step['go_to_page'], pages)
                    plan['page'] = step['go_to_page']
                    journal['actions'].append({'step': step, 'observed': pc.active_page(page, pages)})
                else:
                    raise ValueError(f'Unknown step: {step}')
                dump(out / 'evidence/journal.json', journal)
            journal['status'] = 'completed'
        finally:
            browser.close()
            journal['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
            dump(out / 'evidence/journal.json', journal)
    return journal


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--connection', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        journal = run(args.connection, args.plan, args.out)
        print(json.dumps({'status': journal['status'], 'states': [{k: s[k] for k in ('label', 'receipt', 'cards', 'tables', 'date')} for s in journal['states']]}, indent=2, default=str))
    except Exception as exc:
        print(json.dumps({'error': str(exc), 'type': type(exc).__name__,
                          'note': 'The failed receipt and observation are retained; inspect them before retrying into a fresh output'}), file=sys.stderr)
        sys.exit(1)
