"""Fail-closed checks of captured UI state; not an oracle for business meaning."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from connections import digest, dump, ensure_evidence_writable


def resolve_pointer(document, pointer):
    if pointer == '':
        return document
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('Expected a JSON pointer')
    value = document
    for raw in pointer[1:].split('/'):
        key = raw.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            if not key.isdigit():
                raise KeyError(key)
            value = value[int(key)]
        else:
            value = value[key]
    return value


def same_value(actual, wanted):
    if type(actual) is not type(wanted):
        return False
    if isinstance(actual, dict):
        return actual.keys() == wanted.keys() and all(same_value(actual[k], wanted[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(wanted) and all(same_value(a, b) for a, b in zip(actual, wanted))
    return actual == wanted


def compare(observation, expected):
    if not isinstance(expected, dict) or not expected:
        raise ValueError('At least one explicit state expectation is required')
    results = []
    for pointer, wanted in expected.items():
        try:
            actual = resolve_pointer(observation, pointer)
            matched = same_value(actual, wanted)
            results.append({'pointer': pointer, 'expected': wanted, 'actual': actual,
                            'status': 'passed' if matched else 'failed'})
        except (KeyError, IndexError, TypeError):
            results.append({'pointer': pointer, 'expected': wanted,
                            'status': 'failed', 'error': 'Missing observation'})
    return results


def check_state(observation_file, expected, output):
    source, out = Path(observation_file).resolve(), Path(output).resolve()
    ensure_evidence_writable(out)
    if out.exists():
        raise ValueError('Preserve prior checks; choose a new receipt')
    observation = json.loads(source.read_text(encoding='utf-8-sig'))
    checks = compare(observation, expected)
    receipt = {'kind': 'state_check', 'at': datetime.now(timezone.utc).isoformat(),
               'observation': os.path.relpath(source, out.parent).replace('\\', '/'),
               'observation_sha256': digest(source), 'expected': expected, 'checks': checks,
               'status': 'passed' if all(c['status'] == 'passed' for c in checks) else 'failed',
               'limit': 'Checks recorded fields only. Actual capture provenance, full filter context and business correctness need investigation.'}
    dump(out, receipt)
    if receipt['status'] != 'passed':
        raise AssertionError('State mismatch. Receipt retained at ' + str(out))
    return receipt


def verify_receipt(receipt_file, case_root=None):
    path = Path(receipt_file).resolve()
    receipt = json.loads(path.read_text(encoding='utf-8-sig'))
    if receipt.get('kind') != 'state_check':
        raise ValueError('Not a state-check receipt')
    source = (path.parent / receipt['observation']).resolve()
    if case_root and not source.is_relative_to(Path(case_root).resolve()):
        raise ValueError('State observation escapes the case')
    if digest(source) != receipt['observation_sha256']:
        raise ValueError('State observation changed after checking')
    checks = compare(json.loads(source.read_text(encoding='utf-8-sig')), receipt['expected'])
    if checks != receipt['checks']:
        raise ValueError('State-check results do not match the observation')
    status = 'passed' if all(c['status'] == 'passed' for c in checks) else 'failed'
    if receipt.get('status') != status:
        raise ValueError('State-check status was incorrectly recorded')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--observation', required=True)
    parser.add_argument('--expected', required=True, help='JSON file mapping pointers to exact values')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    expectations = json.loads(Path(args.expected).read_text(encoding='utf-8-sig'))
    result = check_state(args.observation, expectations, args.out)
    print(json.dumps({'receipt': args.out, 'status': result['status'], 'checks': result['checks']}))
