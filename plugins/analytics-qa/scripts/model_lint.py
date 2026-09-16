"""Deterministic lint over captured Power BI model metadata and report layout.

Input: the directory written by `pbi.py model` (TMSCHEMA_*.json) and optionally
the legacy `report.json` layout (from pbix_snapshot.py) for label checks.
Output: a findings file, one entry per detector hit, each with the method id,
severity, the object, the literal expression fragment and why it matters.

These are review leads with evidence, not verdicts: a TODAY() in a measure can
be intended. The agent must turn each finding into a claim with its context.

  python model_lint.py --model <case>/evidence/model --report discovery/report/report.json --out <run>/lint.json
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connections import dump, digest  # noqa: E402

VOLATILE = re.compile(r'\b(TODAY|NOW|UTCNOW|UTCTODAY)\s*\(', re.I)
DATE_CONTEXT_REMOVAL = re.compile(r'\b(REMOVEFILTERS|ALLSELECTED|ALLEXCEPT|ALL)\s*\(\s*\'?(dim_date|date|calendar|dates|Date|Calendar)[^)]*\)', re.I)
RAW_DIVISION = re.compile(r'(\]|\))\s*/\s*(\[|\(|[A-Za-z])')
STATISTIC = re.compile(r'\b(MEDIAN|MEDIANX|PERCENTILE\.INC|PERCENTILE\.EXC|PERCENTILEX\.INC|AVERAGE|AVERAGEX|DIVIDE)\s*\(', re.I)
INTEGER_FORMAT = re.compile(r'^\s*"?(#,##0|#,0|0)(?![.0-9])')  # integer formats only; "0.00%" keeps precision
FRESHNESS_WORDS = re.compile(r'refresh|updated|as of|last load|data through', re.I)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def rows(model_dir, name):
    path = Path(model_dir) / f'{name}.json'
    if not path.is_file():
        return []
    data = load(path)
    return data.get('rows', [])


def lint_model(model_dir):
    tables = {r['ID']: r['Name'] for r in rows(model_dir, 'TMSCHEMA_TABLES')}
    findings = []
    for column in rows(model_dir, 'TMSCHEMA_COLUMNS'):
        if column.get('Type') != 2 or not column.get('Expression'):
            continue
        name = f"{tables.get(column['TableID'], '?')}[{column.get('ExplicitName')}]"
        expression = column['Expression']
        if VOLATILE.search(expression):
            findings.append({'method': 'lint.volatile_time_in_column', 'severity': 'high', 'object': name, 'kind': 'calculated_column',
                             'fragment': expression.strip()[:240],
                             'why': 'A calculated column is evaluated when the model is processed or recalculated, not when a visual queries it; a TODAY()/NOW() column freezes the clock at refresh time and can also be re-evaluated on open, so flags such as "completed month" drift away from the imported data.'})
    for measure in rows(model_dir, 'TMSCHEMA_MEASURES'):
        name = f"{tables.get(measure['TableID'], '?')}[{measure.get('Name')}]"
        expression = measure.get('Expression') or ''
        fmt = measure.get('FormatString') or ''
        if VOLATILE.search(expression):
            findings.append({'method': 'lint.volatile_time_in_measure', 'severity': 'medium', 'object': name, 'kind': 'measure',
                             'fragment': expression.strip()[:240],
                             'why': 'The result depends on the machine clock at query time, not on the imported data; screenshots taken on different days differ and a stale import silently shows an empty recent window.'})
        if DATE_CONTEXT_REMOVAL.search(expression):
            findings.append({'method': 'lint.date_context_removed', 'severity': 'medium', 'object': name, 'kind': 'measure',
                             'fragment': DATE_CONTEXT_REMOVAL.search(expression).group(0)[:160],
                             'why': 'The measure removes or widens the date filter; under a partial-period slicer it can add whole-month or all-time amounts. Confirm the intended period semantics with a partial range.'})
        if RAW_DIVISION.search(expression) and 'DIVIDE' not in expression.upper():
            findings.append({'method': 'lint.raw_division', 'severity': 'low', 'object': name, 'kind': 'measure',
                             'fragment': expression.strip()[:160],
                             'why': 'Division with / returns infinity or an error when the denominator is zero or BLANK; DIVIDE() with an explicit alternate result makes empty slices explicit.'})
        if FRESHNESS_WORDS.search(measure.get('Name') or '') and not re.search(r'refresh|partition|NOW\(|UTCNOW', expression, re.I):
            findings.append({'method': 'lint.freshness_label_on_business_date', 'severity': 'medium', 'object': name, 'kind': 'measure',
                             'fragment': expression.strip()[:160],
                             'why': 'The measure is named as a refresh/freshness indicator but computes from business data (for example MAX of a business date); it changes with slicers and does not report when the model was refreshed.'})
        if STATISTIC.search(expression) and INTEGER_FORMAT.match(fmt):
            findings.append({'method': 'lint.format_hides_precision', 'severity': 'low', 'object': name, 'kind': 'measure',
                             'fragment': f'format "{fmt}" on {STATISTIC.search(expression).group(0)}...',
                             'why': 'The format string rounds a statistic (median, percentile, average, ratio) to an integer; the displayed value differs from the engine value and can hide small changes.'})
    relationships = rows(model_dir, 'TMSCHEMA_RELATIONSHIPS')
    columns = {c['ID']: (tables.get(c['TableID']), c.get('ExplicitName')) for c in rows(model_dir, 'TMSCHEMA_COLUMNS')}
    paths = {}
    for rel in relationships:
        frm, to = columns.get(rel['FromColumnID']), columns.get(rel['ToColumnID'])
        if frm and to:
            paths.setdefault((frm[0], to[0]), []).append({'from': frm[1], 'to': to[1], 'active': rel.get('IsActive')})
    for (fact, dim), items in paths.items():
        if len(items) > 1:
            findings.append({'method': 'lint.multiple_relationship_paths', 'severity': 'medium', 'object': f'{fact} -> {dim}', 'kind': 'relationship',
                             'fragment': json.dumps(items),
                             'why': 'Several relationships between the same tables (for example first-touch and last-touch attribution) mean a measure silently uses the active one; visuals labelled with one attribution lens can compute another unless USERELATIONSHIP is explicit.'})
    return findings


def lint_report(report_path, model_dir):
    """Label checks on a report layout (legacy report.json or PBIR definition folder): stale query names, unresolved measures, freshness titles."""
    from powerbi_inventory import visual_bindings
    report_path = Path(report_path)
    report_dir = report_path if report_path.is_dir() else report_path.parent
    tables = {r['ID']: r['Name'] for r in rows(model_dir, 'TMSCHEMA_TABLES')}
    measures = {m['Name']: (tables.get(m['TableID']), m.get('Expression') or '') for m in rows(model_dir, 'TMSCHEMA_MEASURES')}
    findings = []
    for binding in visual_bindings(report_dir):
        where = f"{binding.get('page')}/{binding.get('visual_id')}"
        title = ''
        for obj in binding.get('title_properties') or []:
            text = (obj.get('properties', {}).get('text', {}).get('expr', {}).get('Literal', {}).get('Value')) if isinstance(obj, dict) else None
            if text:
                title = str(text).strip("'")
        pairs = []
        if binding.get('format') == 'PBIR':
            for role, projections in (binding.get('projections') or {}).items():
                for item in projections:
                    field = item.get('field', {})
                    measure = field.get('Measure', {})
                    if measure:
                        pairs.append((measure.get('Property'), item.get('queryRef') or item.get('nativeQueryRef')))
        else:
            for item in (binding.get('query') or {}).get('Select', []):
                measure = item.get('Measure', {})
                if measure:
                    pairs.append((measure.get('Property'), item.get('Name')))
        for prop, query_name in pairs:
            if prop and query_name and prop not in query_name and str(query_name).split('.')[-1] != prop:
                findings.append({'method': 'lint.stale_query_name', 'severity': 'low', 'object': where, 'kind': 'visual',
                                 'fragment': f'projection "{query_name}" -> measure "{prop}"',
                                 'why': 'The visual still carries the measure\'s old query name; column headers and legends derived from it can show a different word than the bound measure.'})
            if prop and prop not in measures:
                findings.append({'method': 'lint.unresolved_measure', 'severity': 'medium', 'object': where, 'kind': 'visual',
                                 'fragment': prop, 'why': 'The bound measure name does not exist in the captured model; the visual may be broken or bound to a renamed measure.'})
            if prop in measures and FRESHNESS_WORDS.search(title or '') and not re.search(r'refresh|partition|NOW\(|UTCNOW', measures[prop][1], re.I):
                findings.append({'method': 'lint.freshness_label_on_business_date', 'severity': 'medium', 'object': where, 'kind': 'visual',
                                 'fragment': f'title "{title}" bound to {prop} = {measures[prop][1].strip()[:120]}',
                                 'why': 'A label that reads as a refresh time is bound to a business-date measure; readers will take selected data recency for model freshness.'})
    return findings


def run(model_dir, report_path, out):
    out = Path(out)
    if out.exists():
        raise ValueError('Preserve prior lint output; use a fresh path')
    findings = lint_model(model_dir)
    if report_path:
        findings += lint_report(report_path, model_dir)
    seen, unique = set(), []
    for f in findings:
        key = (f['method'], f['object'], f['fragment'])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    summary = {}
    for f in unique:
        summary[f['method']] = summary.get(f['method'], 0) + 1
    result = {'kind': 'model_lint', 'at': dt.datetime.now(dt.timezone.utc).isoformat(), 'model_dir': str(Path(model_dir).resolve()),
              'report': str(Path(report_path).resolve()) if report_path else None,
              'inputs_sha256': {p.name: digest(p) for p in sorted(Path(model_dir).glob('TMSCHEMA_*.json'))},
              'summary': summary, 'findings': unique,
              'note': 'Review leads with literal fragments; each needs an agent claim with context. No finding is proof of a defect; an empty list is not proof of correctness.'}
    dump(out, result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--model', required=True, help='directory written by pbi.py model')
    p.add_argument('--report', help='report snapshot: legacy report.json or a PBIR snapshot folder (pbix_snapshot.py) for label checks')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    result = run(a.model, a.report, a.out)
    print(json.dumps({'out': a.out, 'summary': result['summary'], 'findings': len(result['findings'])}, indent=2))
