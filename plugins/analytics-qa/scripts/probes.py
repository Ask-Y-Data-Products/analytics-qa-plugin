"""Run failure-mode probes from a plan: DAX on the live model, then a method from stats_lib or an assertion.

The agent selects methods from detectors/catalog.json for the component under
review, instantiates their DAX templates for the exact filter context, and lists
them in a probe plan. This runner executes each probe, applies the method and
writes one evidence file per probe plus a journal. Statuses: passed, failed,
review (a statistical flag that needs explanation), inconclusive.

Plan (JSON):
{
  "component": "meta_daily",
  "probes": [
    {"id": "meta-ratio-bound", "method": "stat.ratio_stability",
     "dax": "EVALUATE ...",                       # returns key/numerator/denominator rows
     "params": {"key_column": "dim_date[date_key]", "numerator": "[MetaCRM]", "denominator": "[MetaPlatform]",
                "minimum_denominator": 30, "upper_bound": 1.0},
     "question": "Are CRM Meta leads ever more than platform leads on a day?"},
    {"id": "leads-weekday", "method": "stat.weekday_robust_band",
     "dax": "EVALUATE ...", "params": {"key_column": "dim_date[date_key]", "value_column": "[Leads]"}},
    {"id": "leads-additive", "method": "dax.additivity",
     "dax": "EVALUATE ...",                       # rows with a member column and a value column, plus a total row
     "params": {"member_column": "dim_channel[channel_name]", "value_column": "[Leads]", "total_dax": "EVALUATE ROW(\\"Total\\", [Leads])", "total_column": "[Total]"}},
    {"id": "inq-fanout", "method": "dax.fan_out", "dax": "EVALUATE ROW(\\"Rows\\", COUNTROWS(f), \\"Joined\\", ..., \\"Keys\\", DISTINCTCOUNT(f[id]))",
     "params": {"base": "[Rows]", "joined": "[Joined]", "distinct_keys": "[Keys]"}},
    {"id": "campaign-window", "method": "dax.assert", "dax": "EVALUATE ROW(\\"Outside\\", ...)",
     "params": {"column": "[Outside]", "operator": "==", "value": 0}, "severity": "medium"}
  ]
}
Column names follow DAX result naming: alias "Leads" in ADDCOLUMNS/ROW is returned as "[Leads]",
a grouping column as "dim_date[date_key]". A BLANK count is treated as 0 (blank_as_zero).

Pass `--model <dir>` (the directory written by `pbi.py model`, holding TMSCHEMA_TABLES.json,
TMSCHEMA_COLUMNS.json and TMSCHEMA_MEASURES.json) to check every 'Table'[Column] and
[Measure] reference against the real model before spending a round trip: a probe whose DAX
names a column the model does not have is recorded inconclusive with the closest real name,
and its DAX is not executed. A probe whose DAX still carries a catalog template placeholder
(<fact>, <date_key>) is refused the same way, with or without --model. Fix the name and rerun
into a fresh run directory; an inconclusive probe caused by a naming error is not a finding.

Usage: python probes.py --connection connection.json --plan probes.json --out <fresh directory> [--model <model dir>]
"""
import argparse
import datetime as dt
import difflib
import json
import operator
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connections import dump, ensure_evidence_writable  # noqa: E402
import stats_lib  # noqa: E402

OPERATORS = {'==': operator.eq, '!=': operator.ne, '<': operator.lt, '<=': operator.le, '>': operator.gt, '>=': operator.ge}
QUALIFIED_REF = re.compile(r"(?:'([^'\r\n]+)'|(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*))\s*\[([^\]\r\n]+)\]")
BRACKETED = re.compile(r'\[([^\]\r\n]+)\]')
DEFINED_MEASURE = re.compile(r"\bMEASURE\s+(?:'[^'\r\n]+'|[A-Za-z_][A-Za-z0-9_]*)\s*\[([^\]\r\n]+)\]", re.I)
ALIAS = re.compile(r'"([^"\r\n]*)"')
PLACEHOLDER = re.compile(r'<[A-Za-z_][A-Za-z0-9_]*>')
ERROR_IDENTIFIER = re.compile(r"'([^'\r\n]{1,80})'|\"([^\"\r\n]{1,80})\"")


def load_model(model_dir):
    """Table -> columns and the measure names, from a `pbi.py model` capture directory."""
    folder = Path(model_dir)

    def rows(name):
        path = folder / f'{name}.json'
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        return data.get('rows', []) if isinstance(data, dict) else data

    names = {r.get('ID'): r.get('Name') for r in rows('TMSCHEMA_TABLES')}
    model = {'tables': {name: [] for name in names.values() if name}, 'measures': []}
    for column in rows('TMSCHEMA_COLUMNS'):
        table = names.get(column.get('TableID'))
        column_name = column.get('ExplicitName') or column.get('InferredName')
        if table and column_name:
            model['tables'].setdefault(table, []).append(column_name)
    for measure in rows('TMSCHEMA_MEASURES'):
        if measure.get('Name'):
            model['measures'].append(measure['Name'])
    return model


def listed(names, limit=8):
    names = nameable(names)
    return ', '.join(names[:limit]) + (', ...' if len(names) > limit else '')


def nameable(names):
    """Names worth showing a human: the engine's RowNumber-<guid> key columns are not."""
    return [n for n in names if not str(n).upper().startswith('ROWNUMBER-')]


def closest(name, candidates):
    options = {c.lower(): c for c in nameable(candidates)}
    hits = difflib.get_close_matches((name or '').lower(), list(options), n=1, cutoff=0.6)
    return f' (closest: {options[hits[0]]})' if hits else ''


def check_dax_references(dax, model):
    """Report 'Table'[Column] / [Measure] references the model contradicts, with the closest real name.

    Deliberately lenient: a bare [X] that is a measure, a locally defined measure,
    a query alias or a column of a table the query already references is accepted,
    and nothing is reported when the model capture is empty.
    """
    tables = {name.lower(): (name, list(columns)) for name, columns in ((model or {}).get('tables') or {}).items()}
    if not tables:
        return []
    measures = {m.lower() for m in (model or {}).get('measures') or []}
    text = dax or ''
    local = {n.strip().lower() for n in DEFINED_MEASURE.findall(text)}
    local |= {a.strip().lower() for a in ALIAS.findall(text)}  # ADDCOLUMNS/ROW aliases are read back as [Alias]
    scan = DEFINED_MEASURE.sub(' ', text)
    problems, referenced = [], set()
    remainder = scan
    for match in QUALIFIED_REF.finditer(scan):
        remainder = remainder.replace(match.group(0), ' ')
        table, column = (match.group(1) or match.group(2) or '').strip(), match.group(3).strip()
        known = tables.get(table.lower())
        if known is None:
            problems.append(f"Unknown table '{table}'; model has: {listed(sorted(n for n, _ in tables.values()))}"
                            f"{closest(table, [n for n, _ in tables.values()])}")
            continue
        actual, columns = known
        referenced.add(actual.lower())
        if column.lower() in {c.lower() for c in columns} or column.lower() in measures or column.lower() in local:
            continue
        problems.append(f"Unknown column '{actual}'[{column}]; {actual} has: {listed(columns)}{closest(column, columns)}")
    visible = {c.lower() for table in referenced for c in tables[table][1]}
    for name in (n.strip() for n in BRACKETED.findall(remainder)):
        if name.lower() in measures or name.lower() in local or name.lower() in visible:
            continue
        candidates = list((model or {}).get('measures') or []) + [c for t in referenced for c in tables[t][1]]
        problems.append(f"Unknown measure [{name}]; model measures include: "
                        f"{listed(sorted((model or {}).get('measures') or []))}{closest(name, candidates)}")
    return list(dict.fromkeys(problems))


def unfilled_placeholders(probe):
    """Catalog templates ship with <fact>/<date_key> holes; an unfilled one is a plan error, not a result."""
    text = (probe.get('dax') or '') + ' ' + json.dumps(probe.get('params', {}), default=str)
    return [f'template placeholder {p} not filled' for p in dict.fromkeys(PLACEHOLDER.findall(text))]


def suggest_from_error(message, model):
    """Closest real model names for identifiers a DAX engine error quotes."""
    tables = (model or {}).get('tables') or {}
    if not tables:
        return []
    known = {}
    for table, columns in tables.items():
        known.setdefault(table.lower(), table)
        for column in columns:
            known.setdefault(column.lower(), column)
    for measure in (model or {}).get('measures') or []:
        known.setdefault(measure.lower(), measure)
    notes = []
    for quoted, double in ERROR_IDENTIFIER.findall(message or ''):
        token = (quoted or double).strip()
        if not token or token.lower() in known:
            continue
        hits = difflib.get_close_matches(token.lower(), list(known), n=3, cutoff=0.6)
        if hits:
            notes.append(f"Engine mentioned '{token}'; closest model names: " + ', '.join(known[h] for h in hits))
    return list(dict.fromkeys(notes))


def run_method(probe, result_rows, dax_runner):
    method = probe['method']
    params = probe.get('params', {})
    if method == 'stat.weekday_robust_band':
        points = stats_lib.series(result_rows, params['key_column'], params['value_column'])
        outcome = stats_lib.weekday_robust_band(points, params.get('threshold', 3.5), params.get('minimum_per_weekday', 4))
    elif method == 'stat.robust_zscores':
        points = stats_lib.series(result_rows, params['key_column'], params['value_column'])
        outcome = stats_lib.robust_zscores(points, params.get('threshold', 3.5), params.get('minimum_points', 8))
    elif method == 'stat.changepoints':
        points = stats_lib.series(result_rows, params['key_column'], params['value_column'])
        outcome = stats_lib.changepoints(points, params.get('minimum_segment', 7), params.get('minimum_shift_sigma', 2.0))
    elif method == 'stat.ratio_stability':
        outcome = stats_lib.ratio_stability(result_rows, params['numerator'], params['denominator'], params['key_column'],
                                            params.get('minimum_denominator', 30), params.get('iqr_multiplier', 1.5), params.get('upper_bound'))
    elif method == 'stat.population_stability':
        baseline = {r[params['label_column']]: float(r[params['value_column']] or 0) for r in result_rows if r.get(params['period_column']) == params['baseline_period']}
        current = {r[params['label_column']]: float(r[params['value_column']] or 0) for r in result_rows if r.get(params['period_column']) == params['current_period']}
        outcome = stats_lib.population_stability(baseline, current, params.get('threshold', 0.25))
    elif method == 'stat.ratio_of_totals_vs_mean_of_ratios':
        outcome = stats_lib.ratio_of_totals_vs_mean_of_ratios(result_rows, params['numerator'], params['denominator'])
    elif method == 'dax.additivity':
        parts = {r[params['member_column']]: r[params['value_column']] for r in result_rows}
        total_rows = dax_runner(params['total_dax'])['rows']
        total = total_rows[0][params['total_column']]
        outcome = stats_lib.additivity(parts, total, params.get('tolerance', 0.5))
    elif method == 'dax.fan_out':
        row = result_rows[0]
        outcome = stats_lib.fan_out(row[params['base']], row[params['joined']], row.get(params['distinct_keys']) if params.get('distinct_keys') else None)
    elif method == 'dax.assert':
        actual = result_rows[params.get('row', 0)][params['column']]
        if actual is None and params.get('blank_as_zero', True):
            actual = 0  # COUNTROWS of an empty table is BLANK, which means zero for an invariant count
        wanted = params['value']
        op = OPERATORS[params.get('operator', '==')]
        tolerance = params.get('tolerance')
        if tolerance is not None and params.get('operator', '==') == '==':
            ok = actual is not None and abs(float(actual) - float(wanted)) <= float(tolerance)
        else:
            ok = actual is not None and op(actual, wanted)
        outcome = {'method': 'dax.assert', 'status': 'clean' if ok else 'flagged', 'actual': actual, 'operator': params.get('operator', '=='), 'expected': wanted, 'tolerance': tolerance}
    else:
        raise ValueError(f'Unknown method {method}')
    return outcome


STATUS_MAP = {'clean': 'passed', 'flagged': 'review', 'inconclusive': 'inconclusive', 'informational': 'inconclusive'}


def run(connection, plan_path, out, dax_runner=None, model=None):
    out = Path(out).resolve()
    ensure_evidence_writable(out)
    if (out / 'evidence').exists():
        raise ValueError('Output root already holds evidence; choose a fresh directory')
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8-sig'))
    conn = json.loads(Path(connection).read_text(encoding='utf-8-sig')) if connection else {}
    if model is not None and not isinstance(model, dict):
        model = load_model(model)
    if dax_runner is None:
        from pbi import dax as engine_dax
        dax_runner = lambda text: engine_dax(conn['port'], text, conn['database'])
    journal = {'kind': 'probes', 'started_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'component': plan.get('component'),
               'connection': {k: conn.get(k) for k in ('port', 'database', 'report')}, 'plan': plan,
               'model_checked': bool(model), 'probes': [], 'status': 'running'}
    for probe in plan['probes']:
        record = {'id': probe['id'], 'method': probe['method'], 'question': probe.get('question', ''), 'severity': probe.get('severity', 'review')}
        unknown = unfilled_placeholders(probe) + (check_dax_references(probe.get('dax'), model) if model else [])
        if unknown:
            error = '; '.join(unknown)
            evidence = {'kind': 'probe', 'id': probe['id'], 'method': probe['method'], 'question': probe.get('question', ''),
                        'params': probe.get('params', {}), 'dax': probe.get('dax'), 'status': 'inconclusive', 'error': error,
                        'unknown_references': unknown, 'executed': False,
                        'note': 'The DAX was not executed: it names objects the captured model does not have, or still holds a '
                                'template placeholder. Correct the names and rerun into a fresh run directory; a probe left '
                                'inconclusive by a naming error is not a finding.'}
            record.update({'status': 'inconclusive', 'error': error, 'unknown_references': unknown})
            dump(out / 'evidence/probes' / f"{probe['id']}.json", evidence)
            journal['probes'].append(record)
            continue
        try:
            result = dax_runner(probe['dax'])
            outcome = run_method(probe, result['rows'], dax_runner)
            status = STATUS_MAP.get(outcome.get('status'), 'inconclusive')
            if probe['method'] == 'dax.assert':
                status = 'passed' if outcome['status'] == 'clean' else 'failed'
            evidence = {'kind': 'probe', 'id': probe['id'], 'method': probe['method'], 'question': probe.get('question', ''),
                        'params': probe.get('params', {}), 'dax': probe['dax'], 'query_result': result, 'outcome': outcome, 'status': status,
                        'interpretation_required': status in ('review', 'inconclusive'),
                        'note': 'A flagged statistic is a review lead; a clean statistic is not proof. dax.assert probes pass or fail on the stated expectation.'}
            record.update({'status': status, 'summary': {k: v for k, v in outcome.items() if k in ('status', 'flagged', 'shifts', 'psi', 'remainder', 'multiplier', 'duplicate_rows', 'actual', 'expected')}})
        except Exception as exc:  # execution error is not a failed assertion
            error = '; '.join([str(exc)] + suggest_from_error(str(exc), model))
            evidence = {'kind': 'probe', 'id': probe['id'], 'method': probe['method'], 'dax': probe.get('dax'), 'status': 'inconclusive', 'error': error}
            record.update({'status': 'inconclusive', 'error': error})
        dump(out / 'evidence/probes' / f"{probe['id']}.json", evidence)
        journal['probes'].append(record)
    journal['status'] = 'completed'
    journal['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    dump(out / 'evidence/journal.json', journal)
    return journal


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--connection', required=True)
    p.add_argument('--plan', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--model', help='pbi.py model capture directory (TMSCHEMA_*.json); checks DAX names before executing')
    a = p.parse_args()
    journal = run(a.connection, a.plan, a.out, model=a.model)
    print(json.dumps({'status': journal['status'], 'probes': journal['probes']}, indent=2, default=str))
