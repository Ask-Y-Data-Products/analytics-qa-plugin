"""Serve a sign-off page on loopback and turn the analyst's own decisions into a capture report.

Without this, a review costs a round trip: the analyst downloads a decisions file,
somebody finds it, runs `qa.py review`, renders the revision and sends the result
back. In a live session that is too slow and too manual, and every hand-off is a
chance to lose or edit the file.

The server closes the loop on one machine. It serves the sign-off page and the
sealed screenshots it references over 127.0.0.1 only, read-only, refusing any path
outside the page's own folder. It injects nothing into the file on disk: the
endpoint is announced to the page as it is served, so the same file opened from
disk still downloads the decisions exactly as before. A posted decisions array is
validated against the sealed case - known expectations, the case's own decision
vocabulary, a reviewer, the sealed manifest digest - and only then written beside
the case and handed to `qa.py`'s own `review_revision`, which seals the review
revision and renders its report. Nothing here writes into the sealed case, and
nothing here decides anything: the reviewer string is recorded exactly as it was
typed, and the response says plainly that this is a local attestation and not an
authenticated signature.

  validate_payload(case, payload) -> (decisions, errors)
  apply_decisions(case, decisions, out) -> summary of the sealed review revision
  build_server(case, page, port, out) -> an http.server ready to serve_forever()
"""
import argparse
import datetime as dt
import json
import mimetypes
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, unquote

import qa
from connections import digest, dump

ATTESTATION = ('Recorded from the name typed on this page, on this machine. This is a local '
               'attestation, not an authenticated signature.')
# Announced to the page as it is served; the file on disk is never modified.
ENDPOINT, STATUS_PATH, REPORT_PREFIX = '/decisions', '/status', '/report/'
INJECTION = ('<script>window.ANALYTICS_QA_REVIEW={"endpoint":"%s","status":"%s"};</script>'
             % (ENDPOINT, STATUS_PATH))
# The page's own payload script; the announcement has to come before it.
MARKER = '<script type="application/json" id="payload">'
MAX_BODY = 4 * 1024 * 1024


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


# --- the sealed case -------------------------------------------------------

def load_case(case_path):
    """What a decision has to agree with: the case's expectations, vocabulary and digest.

    Verifies the seal first, so decisions can never be recorded against evidence
    that changed since the page was produced.
    """
    case_dir = Path(case_path).resolve()
    qa.verify(case_dir)
    case = qa.read(case_dir / 'case.json')
    return {'dir': case_dir, 'id': case['id'],
            'claims': {c['id']: c for c in case['claims']},
            'manifest_sha256': digest(case_dir / 'manifest.json'),
            'vocabulary': list(qa.REVIEW_DECISIONS)}


def context(case):
    """Accept either a sealed case directory or an already loaded context."""
    if isinstance(case, dict) and 'manifest_sha256' in case:
        return case
    return load_case(case)


# --- validation ------------------------------------------------------------

CARRIED = ('claim_id', 'decision', 'reviewer', 'comment', 'component_id',
           'reviewed_manifest_sha256', 'confirmation', 'reason', 'scope', 'expires_at')


def validate_payload(case, payload):
    """`(decisions, errors)` for one posted decisions array. Never raises, never guesses.

    The array is the one the sign-off page already exports. Every entry has to name
    an expectation this case carries, a decision from the case's own vocabulary, a
    reviewer, the digest of the sealed manifest the page was built from, and its
    confirmation provenance. `accepted` stays impossible on an expectation that did
    not pass, exactly as `qa.py review` refuses it, so the reviewer is told here
    rather than by a stack trace later. Decisions come back only when the whole
    array is good; otherwise every complaint is returned at once.
    """
    case = context(case)
    if not isinstance(payload, list) or not payload:
        return [], ['Expected a non-empty JSON array of decisions, exactly as the sign-off page exports it.']
    errors, decisions, seen = [], [], set()
    for position, entry in enumerate(payload):
        where = f'decision {position + 1}'
        if not isinstance(entry, dict):
            errors.append(f'{where}: expected an object, got {type(entry).__name__}')
            continue
        claim_id, decision = entry.get('claim_id'), entry.get('decision')
        if claim_id not in case['claims']:
            errors.append(f'{where}: {claim_id!r} is not an expectation of case {case["id"]}')
        elif claim_id in seen:
            errors.append(f'{where}: {claim_id} was decided more than once')
        seen.add(claim_id)
        if decision not in case['vocabulary']:
            errors.append(f'{where}: decision {decision!r} is not one of {case["vocabulary"]}')
        elif decision == 'accepted' and case['claims'].get(claim_id, {}).get('status') != 'passed':
            errors.append(f'{where}: {claim_id} did not pass, so it cannot be accepted; confirm the defect '
                          'or leave it unresolved')
        elif decision == 'exception' and not all(entry.get(key) for key in ('reason', 'scope', 'expires_at')):
            errors.append(f'{where}: an exception needs reason, scope and expires_at')
        if not str(entry.get('reviewer') or '').strip():
            errors.append(f'{where}: reviewer is empty; the page records the name exactly as it is typed')
        if entry.get('reviewed_manifest_sha256') != case['manifest_sha256']:
            errors.append(f'{where}: reviewed_manifest_sha256 {entry.get("reviewed_manifest_sha256")!r} is not '
                          f'this sealed case ({case["manifest_sha256"]}); reload the sign-off page')
        if not str(entry.get('confirmation') or '').strip():
            errors.append(f'{where}: confirmation provenance is missing')
        decisions.append({key: entry[key] for key in CARRIED if key in entry})
    return ([] if errors else decisions), errors


# --- applying --------------------------------------------------------------

def free_path(path):
    """`path`, or the first `path-2`, `path-3` ... that does not exist yet."""
    path = Path(path)
    if not path.exists():
        return path
    stem, suffix = path.name[:len(path.name) - len(path.suffix)], path.suffix
    for number in range(2, 1000):
        candidate = path.with_name(f'{stem}-{number}{suffix}')
        if not candidate.exists():
            return candidate
    raise ValueError(f'Too many revisions beside {path}')


def apply_decisions(case, decisions, out):
    """Write the decisions beside the case, then let `qa.py` seal and render the revision.

    The revision is built by `review_revision`, not by anything in this file: the
    same validation, the same copy of the reviewed baseline, the same seal.
    """
    case = context(case)
    out = Path(out).resolve()
    path = free_path(case['dir'].parent / f'{case["dir"].name}-decisions.json')
    dump(path, decisions)
    result = qa.review_revision(case['dir'], path, out)
    counts = {}
    for decision in decisions:
        counts[decision['decision']] = counts.get(decision['decision'], 0) + 1
    return {'revision': str(out), 'report': str(Path(result['report'])), 'decisions_file': str(path),
            'recorded': len(decisions), 'counts': counts,
            'reviewer': decisions[0].get('reviewer'),
            'verified_files': qa.verify(out)['verified_files'],
            'validation_errors': result.get('validation_errors') or [],
            'reviewed_manifest_sha256': case['manifest_sha256'],
            'attestation': ATTESTATION}


# --- serving ---------------------------------------------------------------

def announce(document):
    """The page as served: told that an endpoint exists, before its own script runs."""
    if INJECTION in document:
        return document
    if MARKER in document:
        return document.replace(MARKER, INJECTION + MARKER, 1)
    if '</html>' in document:
        return document.replace('</html>', INJECTION + '</html>', 1)
    return document + INJECTION


def safe_file(root, target):
    """The file `target` asks for inside `root`, or None when it points anywhere else.

    Everything that could leave the folder is refused before the filesystem is
    touched - a parent segment, an absolute path, a Windows drive, a backslash -
    and the resolved path is checked against the resolved root as a backstop, so a
    symlink inside the folder cannot lead out of it either.
    """
    root = Path(root).resolve()
    relative = unquote(urlparse(str(target)).path).lstrip('/')
    if not relative or '\\' in relative or ':' in relative or '\x00' in relative:
        return None
    parts = PurePosixPath(relative).parts
    if not parts or any(part in ('..', '.') for part in parts) or PurePosixPath(relative).is_absolute():
        return None
    try:
        candidate = (root / PurePosixPath(relative)).resolve()
    except (OSError, ValueError):
        return None
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


class ReviewServer(ThreadingHTTPServer):
    """Loopback only, read-only, one sealed case, one sign-off page."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, case, page, out=None):
        super().__init__(address, ReviewHandler)
        self.case = context(case)
        self.page = Path(page).resolve()
        self.root = self.page.parent
        self.out_base = Path(out).resolve() if out else (
            self.case['dir'].parent / f'{self.case["dir"].name}-review')
        self.explicit_out = bool(out)
        self.submissions = []
        self.revisions = {}
        self.lock = threading.Lock()

    def url(self, path=''):
        host, port = self.server_address[0], self.server_address[1]
        return f'http://{host}:{port}/{path.lstrip("/")}'

    def next_output(self):
        if self.explicit_out and not self.out_base.exists():
            return self.out_base
        return free_path(self.out_base)

    def record(self, result):
        """Keep the produced revision servable and reportable, and hand back its URL."""
        name = Path(result['revision']).name
        self.revisions[name] = Path(result['revision'])
        report = REPORT_PREFIX + name + '/' + Path(result['report']).name
        entry = {'at': now(), 'reviewer': result['reviewer'], 'recorded': result['recorded'],
                 'counts': result['counts'], 'revision': result['revision'],
                 'report': result['report'], 'report_url': report}
        self.submissions.append(entry)
        return report


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = 'analytics-qa-review'
    protocol_version = 'HTTP/1.1'

    # --- plumbing ---
    def log_message(self, fmt, *args):  # one quiet line per request
        print(f'[review_server] {self.address_string()} {fmt % args}', flush=True)

    def send_json(self, status, body):
        payload = json.dumps(body, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def send_bytes(self, body, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def refuse(self, status, message):
        self.send_json(status, {'ok': False, 'errors': [message]})

    # --- routes ---
    def do_GET(self):
        path = urlparse(self.path).path
        server = self.server
        if path in ('/', '/index.html', '/' + server.page.name):
            return self.send_bytes(announce(server.page.read_text(encoding='utf-8')).encode('utf-8'),
                                   'text/html; charset=utf-8')
        if path == STATUS_PATH:
            return self.send_json(200, self.status())
        if path.startswith(REPORT_PREFIX):
            return self.serve_revision(path[len(REPORT_PREFIX):])
        found = safe_file(server.root, path)
        if not found:
            return self.refuse(404, f'{path} is not a file of the sign-off page folder')
        kind, _ = mimetypes.guess_type(found.name)
        return self.send_bytes(found.read_bytes(), kind or 'application/octet-stream')

    def do_POST(self):
        if urlparse(self.path).path != ENDPOINT:
            return self.refuse(404, f'{self.path} accepts no decisions; post them to {ENDPOINT}')
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return self.refuse(400, 'Content-Length is not a number')
        if length <= 0 or length > MAX_BODY:
            return self.refuse(400, f'Expected a decisions array of 1 to {MAX_BODY} bytes')
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError) as exc:
            return self.refuse(400, f'The decisions are not readable JSON: {exc}')
        decisions, errors = validate_payload(self.server.case, payload)
        if errors:
            return self.send_json(400, {'ok': False, 'errors': errors})
        with self.server.lock:
            try:
                result = apply_decisions(self.server.case, decisions, self.server.next_output())
            except (OSError, ValueError, KeyError) as exc:
                return self.refuse(409, f'The review revision could not be written: {exc}')
            result['report_url'] = self.server.record(result)
        result['ok'] = True
        return self.send_json(200, result)

    # --- helpers ---
    def serve_revision(self, reference):
        name, _, rest = reference.partition('/')
        folder = self.server.revisions.get(name)
        if not folder:
            return self.refuse(404, 'No review revision has been produced under that name yet')
        found = safe_file(folder, '/' + (rest or 'report.html'))
        if not found:
            return self.refuse(404, f'{rest!r} is not a file of review revision {name}')
        kind, _ = mimetypes.guess_type(found.name)
        return self.send_bytes(found.read_bytes(), kind or 'application/octet-stream')

    def status(self):
        server = self.server
        return {'ok': True, 'case': server.case['id'], 'case_dir': str(server.case['dir']),
                'manifest_sha256': server.case['manifest_sha256'],
                'expectations': len(server.case['claims']), 'vocabulary': server.case['vocabulary'],
                'page': server.page.name, 'endpoint': ENDPOINT,
                'submitted': len(server.submissions), 'submissions': server.submissions,
                'attestation': ATTESTATION,
                'state': 'awaiting actual user decisions' if not server.submissions else 'decisions recorded'}


def build_server(case, page, port=8899, out=None):
    """A ready-to-serve loopback server; port 0 picks a free one (used by the tests)."""
    page = Path(page).resolve()
    if not page.is_file():
        raise ValueError(f'Sign-off page not found: {page}')
    loaded = context(case)
    if page.is_relative_to(loaded['dir']):
        raise ValueError('The sign-off page must live outside the sealed case')
    return ReviewServer(('127.0.0.1', int(port)), loaded, page, out)


def serve(case, page, port=8899, out=None, open_browser=False):
    server = build_server(case, page, port, out)
    print(json.dumps({'url': server.url(), 'case': server.case['id'],
                      'manifest_sha256': server.case['manifest_sha256'],
                      'serving': str(server.root), 'page': server.page.name,
                      'endpoint': ENDPOINT, 'status': STATUS_PATH,
                      'review_output': str(server.out_base),
                      'state': 'awaiting actual user decisions', 'attestation': ATTESTATION}), flush=True)
    if open_browser:
        webbrowser.open(server.url())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--case', required=True, help='the sealed case the page was built from')
    parser.add_argument('--signoff', required=True, help='the sign-off page written by review_form.py')
    parser.add_argument('--port', type=int, default=8899)
    parser.add_argument('--out', help='directory for the sealed review revision (default: <case>-review)')
    parser.add_argument('--open', action='store_true', dest='open_browser',
                        help='open the page in the default browser')
    args = parser.parse_args()
    try:
        serve(args.case, args.signoff, args.port, args.out, args.open_browser)
    except Exception as exc:  # one readable line, like every other script here
        print(json.dumps({'error': str(exc), 'type': type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
