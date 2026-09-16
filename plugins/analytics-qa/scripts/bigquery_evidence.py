"""Read-only BigQuery evidence with active gcloud identity and query-cost bounds."""
import argparse
import datetime as dt
import itertools
import json
from pathlib import Path
import shutil
import subprocess

from connections import dump, ensure_evidence_writable


def client(project):
    from google.cloud import bigquery
    from google.oauth2.credentials import Credentials
    executable = shutil.which('gcloud.cmd') or shutil.which('gcloud')
    if not executable:
        raise RuntimeError('gcloud CLI is required; authenticate it outside this tool')
    token = subprocess.run([executable, 'auth', 'print-access-token'], capture_output=True,
                           text=True, check=True, timeout=60).stdout.strip()
    return bigquery.Client(project=project, credentials=Credentials(token))


def validate_sql(sql, project, datasets):
    import sqlglot
    from sqlglot import exp
    statements = sqlglot.parse(sql, read='bigquery')
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError('Only one read-only SELECT/CTE is allowed')
    tree = statements[0]
    if any(isinstance(n, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Into, exp.Command)) for n in tree.walk()):
        raise ValueError('Write statements are forbidden')
    ctes = {n.alias for n in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not table.catalog and not table.db and table.name in ctes:
            continue
        if table.catalog != project or table.db not in datasets:
            raise ValueError('Use fully qualified tables in the configured project/dataset allowlist')
    for fn in tree.find_all(exp.Anonymous):
        raise ValueError(f'Unrecognized/UDF function is not allowed: {fn.name}')


def query(config, sql):
    from google.cloud import bigquery
    project, datasets = config['project'], config['datasets']
    validate_sql(sql, project, datasets)
    cap = min(int(config.get('maximum_bytes_billed', 1000000000)), 1000000000)
    c = client(project)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    preview = c.query(sql, location=config['location'], job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    if (preview.total_bytes_processed or 0) > cap:
        raise ValueError(f'Dry-run estimate {preview.total_bytes_processed} exceeds cap {cap}; narrow the query')
    job = c.query(sql, location=config['location'], job_config=bigquery.QueryJobConfig(maximum_bytes_billed=cap, use_legacy_sql=False, labels={'purpose': 'analytics-qa'}))
    try:
        result = job.result(timeout=90)
        rows = [dict(row) for row in itertools.islice(result, 5001)]
        columns = [s.name for s in result.schema]
        if len(columns) != len(set(columns)):
            raise ValueError('Duplicate output names would discard evidence')
        return {'kind': 'bigquery', 'started_at': started, 'finished_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'project': project, 'location': job.location, 'job_id': job.job_id, 'sql': sql,
            'schema': [s.to_api_repr() for s in result.schema], 'rows': rows[:5000],
            'complete': len(rows) <= 5000, 'returned_rows': min(len(rows), 5000),
            'dry_run_bytes': preview.total_bytes_processed, 'bytes_processed': job.total_bytes_processed,
            'bytes_billed': job.total_bytes_billed, 'maximum_bytes_billed': cap,
            'source_snapshot': 'Current warehouse at query time, not the historical PBIX import snapshot'}
    except Exception:
        job.cancel()
        raise
    finally:
        c.close()


def metadata(config, table):
    parts = table.split('.')
    if len(parts) != 3 or parts[0] != config['project'] or parts[1] not in config['datasets']:
        raise ValueError('Table is outside the metadata allowlist')
    c = client(config['project'])
    try:
        t = c.get_table(table)
        return {'kind': 'bigquery_table_metadata', 'timestamp': dt.datetime.now(dt.timezone.utc).isoformat(),
            'table': table, 'type': t.table_type, 'schema': [f.to_api_repr() for f in t.schema],
            'created': t.created, 'modified': t.modified, 'rows': t.num_rows,
            'bytes': t.num_bytes, 'view_query': t.view_query,
            'time_partitioning': t.time_partitioning.to_api_repr() if t.time_partitioning else None}
    finally:
        c.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--sql-file')
    source.add_argument('--table')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    ensure_evidence_writable(a.out)
    if Path(a.out).exists():
        raise ValueError('Evidence already exists; use a new path')
    conf = json.loads(Path(a.config).read_text(encoding='utf-8-sig'))
    evidence = query(conf, Path(a.sql_file).read_text(encoding='utf-8-sig')) if a.sql_file else metadata(conf, a.table)
    dump(a.out, evidence)
    print(json.dumps({k: v for k, v in evidence.items() if k not in ['schema', 'rows', 'sql', 'view_query']}, default=str))
