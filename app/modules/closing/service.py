from datetime import datetime, timezone
from time import perf_counter

from app.clock import stamp
from app.config import RULE_VERSION, SAMPLES, ZONE
from app.manifest import canonical, digest
from app.modules.archive.service import sample_sources
from app.shift import load_shift
from .reconcile import reconcile


def sample_book_raw():
    return (SAMPLES / 'book.csv').read_bytes()


def build_manifest(count_raw, book_raw, started):
    identity = {'count_sha256': digest(count_raw), 'book_sha256': digest(book_raw),
                'rule_version': RULE_VERSION}
    closing_id = digest(canonical(identity).encode())
    return {'id': closing_id,
            'inputs': {'count_sheet': {'sha256': identity['count_sha256'], 'bytes': len(count_raw)},
                       'book_sheet': {'sha256': identity['book_sha256'], 'bytes': len(book_raw)}},
            'rule_version': RULE_VERSION, 'zone': ZONE,
            'generated_at': stamp(datetime.now(timezone.utc)),
            'elapsed_ms': round((perf_counter() - started) * 1000, 3)}


def source_identity(count_raw, book_raw):
    return digest(canonical({'count': digest(count_raw), 'book': digest(book_raw)}).encode())


def upload_sheet(store, count_raw, book_raw):
    count_sha256, book_sha256 = digest(count_raw), digest(book_raw)
    source_id = source_identity(count_raw, book_raw)
    source_id, created = store.register_count_sheet(
        source_id, count_raw, book_raw, count_sha256, book_sha256,
        stamp(datetime.now(timezone.utc)))
    return {'source_id': source_id, 'created': created,
            'count_sha256': count_sha256, 'book_sha256': book_sha256}


def preview(store, run_id, count_raw, book_raw):
    run = store.load(run_id)
    shift = load_shift(sample_sources()['shift'])
    return reconcile(count_raw, book_raw, run, shift, perf_counter(), build_manifest)


def close(store, run_id, source_id, expected_closing):
    run = store.load(run_id)
    sheet = store.count_sheet(source_id)
    shift = load_shift(sample_sources()['shift'])
    snapshot = reconcile(sheet['count_raw'], sheet['book_raw'], run, shift,
                         perf_counter(), build_manifest, source_id)
    created = store.save_closing(snapshot, run_id, expected_closing)
    return {'id': snapshot['manifest']['id'], 'run_id': run_id, 'created': created,
            'status': 'active',
            'fleet_base': snapshot['opening_base']['fleet_base'],
            'suspense_held': snapshot['opening_base']['suspense_held']}
