"""Bounded Azure SQL evidence exports. Authentication stays in Azure CLI."""
import datetime as dt
import decimal
import hashlib
import json
from pathlib import Path
import struct


def serial(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(type(value).__name__)


def dump(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=serial, ensure_ascii=True), encoding="utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ensure_evidence_writable(path):
    target = Path(path).resolve()
    for parent in [target, *target.parents]:
        if (parent / "case.json").is_file() and (parent / "manifest.json").is_file():
            raise ValueError(f"Case is sealed: {parent}. Create a new case for additional evidence; finish reset/captures before sealing.")


def connect(config):
    import pyodbc
    from azure.identity import AzureCliCredential
    token = AzureCliCredential().get_token("https://database.windows.net/.default").token.encode("utf-16-le")
    packed = struct.pack("<I", len(token)) + token
    conn = pyodbc.connect(
        f"DRIVER={{{config['driver']}}};SERVER=tcp:{config['server']},1433;"
        f"DATABASE={config['database']};Encrypt=yes;TrustServerCertificate=no;",
        attrs_before={1256: packed}, timeout=30,
    )
    conn.timeout = 60
    return conn


def read_only_sql(sql):
    import sqlglot
    from sqlglot import exp
    trees = sqlglot.parse(sql, read="tsql")
    if len(trees) != 1 or not isinstance(trees[0], exp.Query):
        raise ValueError("Evidence queries must be a single SELECT/CTE query")
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Into, exp.Command)
    if any(isinstance(node, forbidden) for node in trees[0].walk()):
        raise ValueError("Write operations are not permitted in evidence queries")
    return sql


def query(config, sql, limit=5000):
    read_only_sql(sql)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    with connect(config) as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        schema = [{"name": d[0], "type": d[1].__name__, "precision": d[4], "scale": d[5]} for d in cursor.description]
        rows = cursor.fetchmany(limit + 1)
        truncated = len(rows) > limit
        columns = [s["name"] for s in schema]
        if len(set(columns)) != len(columns):
            raise ValueError("Duplicate result column names; use explicit aliases")
        return {"kind": "sql", "started_at": started,
                "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "server": config["server"], "database": config["database"],
                "sql": sql, "schema": schema, "rows": [dict(zip(columns, row)) for row in rows[:limit]],
                "returned_rows": min(len(rows), limit), "complete": not truncated,
                "source_snapshot": None}
