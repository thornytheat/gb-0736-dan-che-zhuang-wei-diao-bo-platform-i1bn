from flask import Blueprint, jsonify, request

from app.config import MAX_INPUT_BYTES
from app.errors import DomainError
from app.modules.closing.service import close, preview, upload_sheet, sample_book_raw
from app.web import store

bp = Blueprint('closing', __name__)


def read_file(field):
    file = request.files.get(field)
    if file is None:
        return None
    raw = file.stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise DomainError('BODY_TOO_LARGE', 413)
    return raw


@bp.get('/api/closing')
def show():
    db = store()
    state = db.closing_state()
    return jsonify(active_run=db.active(), state=state, history=db.closing_history())


@bp.post('/api/closing/sheets')
def upload():
    expected_run = request.form.get('expected_run')
    if not expected_run:
        raise DomainError('BAD_REQUEST', field='expected_run')
    count_raw = read_file('count_sheet')
    if count_raw is None:
        raise DomainError('UPLOAD_REQUIRED')
    book_raw = read_file('book_sheet') or sample_book_raw()
    result = upload_sheet(store(), count_raw, book_raw)
    result['preview'] = preview(store(), expected_run, count_raw, book_raw)
    return jsonify(result)


@bp.post('/api/closing/preview')
def trial():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'run', 'source_id'}:
        raise DomainError('BAD_REQUEST', fields=['run', 'source_id'])
    if not isinstance(data['run'], str) or not isinstance(data['source_id'], str) or not data['run'] or not data['source_id']:
        raise DomainError('BAD_REQUEST')
    sheet = store().count_sheet(data['source_id'])
    snapshot = preview(store(), data['run'], sheet['count_raw'], sheet['book_raw'])
    return jsonify(snapshot)


@bp.post('/api/closing/close')
def settle():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'expected_run', 'expected_closing', 'source_id'}:
        raise DomainError('BAD_REQUEST', fields=['expected_run', 'expected_closing', 'source_id'])
    expected_closing = data['expected_closing']
    if (not isinstance(data['expected_run'], str) or not data['expected_run']
            or not isinstance(data['source_id'], str) or not data['source_id']
            or (expected_closing is not None and
                (not isinstance(expected_closing, str) or not expected_closing))):
        raise DomainError('BAD_REQUEST')
    return jsonify(close(store(), data['expected_run'], data['source_id'], expected_closing))


@bp.get('/api/closing/<closing_id>')
def detail(closing_id):
    return jsonify(store().closing(closing_id))
