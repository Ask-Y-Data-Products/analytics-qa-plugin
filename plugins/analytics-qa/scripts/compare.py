"""Compare typed keyed evidence without silently converting missing or duplicate rows."""
import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

from connections import dump, digest, ensure_evidence_writable


def compare(left, right, keys, values, tolerance="0.005"):
    if not left.get("complete") or not right.get("complete"):
        return {"status": "inconclusive", "reason": "One or both inputs are incomplete"}
    tolerance = Decimal(tolerance)
    if tolerance < 0:
        raise ValueError("Tolerance cannot be negative")
    indexes = []
    for evidence in [left, right]:
        index = {}
        for row in evidence["rows"]:
            key = tuple(row[k] for k in keys)
            if key in index:
                return {"status": "inconclusive", "reason": "Duplicate comparison keys", "key": key}
            index[key] = row
        indexes.append(index)
    a, b = indexes
    deltas = []
    for key in a.keys() & b.keys():
        for field in values:
            av, bv = a[key][field], b[key][field]
            if av is None or bv is None:
                if av != bv:
                    deltas.append({"key": key, "field": field, "before": av, "after": bv, "delta": None})
                continue
            try:
                delta = Decimal(str(bv)) - Decimal(str(av))
            except InvalidOperation as exc:
                raise ValueError(f"Field {field} is not numeric") from exc
            if not delta.is_finite():
                raise ValueError("Non-finite numeric input")
            if abs(delta) > tolerance:
                deltas.append({"key": key, "field": field, "before": av, "after": bv, "delta": str(delta)})
    added, removed = list(b.keys() - a.keys()), list(a.keys() - b.keys())
    return {"status": "different" if deltas or added or removed else "equal", "added_keys": sorted(added, key=str),
            "removed_keys": sorted(removed, key=str), "changes": sorted(deltas, key=lambda d: str((d['key'],d['field']))),
            "tolerance": str(tolerance), "interpretation": "Observed comparison, not a defect verdict. Establish input/code comparability separately."}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--before", required=True); p.add_argument("--after", required=True)
    p.add_argument("--keys", required=True); p.add_argument("--values", required=True)
    p.add_argument("--tolerance", default="0.005"); p.add_argument("--out", required=True)
    a = p.parse_args()
    ensure_evidence_writable(a.out)
    if Path(a.out).exists():
        raise ValueError("Comparison evidence exists; use a fresh output path")
    left = json.loads(Path(a.before).read_text(encoding="utf-8-sig")); right = json.loads(Path(a.after).read_text(encoding="utf-8-sig"))
    result = compare(left, right, a.keys.split(","), a.values.split(","), a.tolerance)
    result.update(before_sha256=digest(a.before), after_sha256=digest(a.after))
    dump(a.out, result)
    print(json.dumps(result))
