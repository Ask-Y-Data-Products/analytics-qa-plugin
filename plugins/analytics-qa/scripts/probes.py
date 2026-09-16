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

Usage: python probes.py --connection connection.json --plan probes.json --out <fresh directory>
"""
import argparse
import datetime as dt
import json
import operator
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connections import dump, ensure_evidence_writable  # noqa: E402
import stats_lib  # noqa: E402

OPERATORS = {'==': operator.eq, '!=': operator.ne, '<': operator.lt, '<=': operator.le, '>': operator.gt, '>=': operator.ge}


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


def run(connection, plan_path, out, dax_runner=None):
    out = Path(out).resolve()
    ensure_evidence_writable(out)
    if (out / 'evidence').exists():
        raise ValueError('Output root already holds evidence; choose a fresh directory')
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8-sig'))
    conn = json.loads(Path(connection).read_text(encoding='utf-8-sig')) if connection else {}
    if dax_runner is None:
        from pbi import dax as engine_dax
        dax_runner = lambda text: engine_dax(conn['port'], text, conn['database'])
    journal = {'kind': 'probes', 'started_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'component': plan.get('component'),
               'connection': {k: conn.get(k) for k in ('port', 'database', 'report')}, 'plan': plan, 'probes': [], 'status': 'running'}
    for probe in plan['probes']:
        record = {'id': probe['id'], 'method': probe['method'], 'question': probe.get('question', ''), 'severity': probe.get('severity', 'review')}
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
            evidence = {'kind': 'probe', 'id': probe['id'], 'method': probe['method'], 'dax': probe.get('dax'), 'status': 'inconclusive', 'error': str(exc)}
            record.update({'status': 'inconclusive', 'error': str(exc)})
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
    a = p.parse_args()
    journal = run(a.connection, a.plan, a.out)
    print(json.dumps({'status': journal['status'], 'probes': journal['probes']}, indent=2, default=str))
