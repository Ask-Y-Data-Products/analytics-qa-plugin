"""Read legacy PBIX report bindings without extracting model data or credentials."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

from connections import digest, dump, ensure_evidence_writable
from powerbi_inventory import visual_bindings


def snapshot(pbix, output):
    source, out = Path(pbix).resolve(), Path(output).resolve()
    ensure_evidence_writable(out)
    if out.exists():
        raise ValueError('Use a new snapshot directory')
    if source.suffix.lower() != '.pbix' or not source.is_file():
        raise ValueError('Expected an existing PBIX file')
    before = digest(source)
    with zipfile.ZipFile(source) as archive:
        matches = [info for info in archive.infolist() if info.filename == 'Report/Layout']
        if len(matches) != 1:
            raise ValueError('No unique legacy Report/Layout. Supply a supported PBIP/PBIR export.')
        if matches[0].file_size > 64 * 1024 * 1024:
            raise ValueError('Report layout exceeds the 64 MiB extraction limit')
        raw = archive.read(matches[0])
    layout = json.loads(raw.decode('utf-16-le').lstrip('\ufeff'))
    if not isinstance(layout, dict) or not isinstance(layout.get('sections'), list):
        raise ValueError('Unsupported report layout structure')
    if digest(source) != before:
        raise ValueError('PBIX changed during extraction; wait for save completion')
    out.mkdir(parents=True)
    dump(out / 'report.json', layout)
    dump(out / 'origin.json', {
        'at': datetime.now(timezone.utc).isoformat(), 'pbix': str(source),
        'pbix_sha256': before, 'layout_member': 'Report/Layout',
        'layout_sha256': hashlib.sha256(raw).hexdigest(),
        'method': 'ZIP member read, UTF-16 LE decode and JSON parse; no model or credential extraction.',
        'runtime': 'Static bindings only; inspect live model and rendered state separately.'})
    bindings = visual_bindings(out)
    dump(out / 'bindings.json', bindings)
    return {'snapshot': str(out), 'pages': len(layout['sections']),
            'visuals': len(bindings), 'pbix_sha256': before}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pbix', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.pbix, args.out)))
