import io
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

from app.manifest import digest
from app.modules.archive.service import sample_plan


COUNT = 'sample_data/count_sheet.csv'
BOOK = 'sample_data/book.csv'


def tmp_book(text):
    path = os.path.join(tempfile.mkdtemp(), 'book.csv')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)
    return path


def upload(client, run, count_path=COUNT, book_path=BOOK):
    with open(count_path, 'rb') as count, open(book_path, 'rb') as book:
        return client.post('/api/closing/sheets', data={
            'expected_run': run,
            'count_sheet': (io.BytesIO(count.read()), 'count.csv'),
            'book_sheet': (io.BytesIO(book.read()), 'book.csv')})


def settle(client, run, source_id, expected_closing=None):
    return client.post('/api/closing/close',
                       json={'expected_run': run, 'expected_closing': expected_closing,
                             'source_id': source_id})


def codes(snapshot):
    return snapshot['disposition_counts']


def by_bike(snapshot, bike_id):
    return next(row for row in snapshot['dispositions'] if row['bike_id'] == bike_id
                and row['code'] != 'CONFLICT_REJECT')


def test_closing_stage_renders_in_workbench(client):
    page = client.get('/')
    assert page.status_code == 200 and '晚班关账' in page.get_data(as_text=True)
    assert client.get('/api/closing').json['state'] is None


def test_every_discrepancy_has_traceable_disposition(client, run_id):
    snapshot = upload(client, run_id).json['preview']
    # 关站暂存有容量关站区间作证据
    staged = by_bike(snapshot, 'B3001')
    assert staged['code'] == 'STAGED_CLOSED' and staged['base_effect'] == 1
    assert staged['evidence']['closure']['station'] == '003'
    # 双站冲突：归属站在开且上报，采信归属站，败方隔离不调账
    winner = by_bike(snapshot, 'B2007')
    reject = next(row for row in snapshot['dispositions']
                  if row['bike_id'] == 'B2007' and row['code'] == 'CONFLICT_REJECT')
    assert winner['code'] == 'MATCHED' and reject['counted_station'] == '004'
    assert reject['evidence']['winner_station'] == '001' and reject['base_effect'] == 0
    # 无来历盘盈与盘亏都挂起，不进基数
    surplus = by_bike(snapshot, 'B2999')
    assert surplus['code'] == 'SURPLUS_SUSPENSE' and surplus['suspended'] and surplus['base_effect'] == 0
    for bike in ('B2010', 'B4004'):
        row = by_bike(snapshot, bike)
        assert row['code'] == 'SHORTAGE_SUSPENSE' and row['suspended'] and row['base_effect'] == 0


def test_parser_isolates_window_and_same_station_duplicates_keeps_cross_station(client, run_id):
    snapshot = upload(client, run_id).json['preview']
    excluded = {(row['line'], row['code']) for row in snapshot['count_sheet']['excluded']}
    assert (15, 'COUNT_OUTSIDE_WINDOW') in excluded
    assert (16, 'COUNT_DUPLICATE_BIKE') in excluded and (17, 'COUNT_DUPLICATE_BIKE') in excluded
    # 跨站重复的 B2007 不进隔离，进入冲突仲裁
    assert all(row['line'] not in (8, 9) for row in snapshot['count_sheet']['excluded'])


def test_opening_base_only_includes_evidenced_adjustments(client, run_id):
    snapshot = upload(client, run_id).json['preview']
    base = snapshot['opening_base']
    assert base['fleet_book'] == 13 and base['fleet_base'] == 13
    assert base['adjusted_bikes'] == 1 and base['suspense_held'] == 3
    # 关站暂存：003 调出 -1、004 调入 +1，各站基数有据可查
    bases = {row['station']: row for row in snapshot['station_bases']}
    assert (bases['003']['base_adjustment'], bases['003']['opening_base']) == (-1, 0)
    assert (bases['004']['base_adjustment'], bases['004']['opening_base']) == (1, 5)
    # 挂起车不凭空调平：001 实盘 7 辆（盘亏 2、冲突剔除 1）但基数仍按账面 8
    assert bases['001']['physical_count'] == 7 and bases['001']['opening_base'] == 8
    assert bases['001']['physical_gap'] == -1


def test_single_report_cross_station_transfer_is_evidenced_and_adjusts_base(client, run_id, tmp_path):
    # B1006 只在 004 盘到（归属 001 未关）：单条跨站上报按 CONFIRMED_TRANSFER 有出处计入基数
    count = open(COUNT, encoding='utf-8').read().replace(
        'B1006,001,2026-11-20T09:50:00+08:00,江湾广场盘平',
        'B1006,004,2026-11-20T09:53:00+08:00,跨站转移单点上报')
    path = tmp_path / 'count.csv'
    path.write_text(count, encoding='utf-8')
    snapshot = upload(client, run_id, count_path=str(path)).json['preview']
    row = by_bike(snapshot, 'B1006')
    assert row['code'] == 'CONFIRMED_TRANSFER' and row['base_effect'] == 1
    bases = {item['station']: item for item in snapshot['station_bases']}
    assert bases['001']['transfer_out'] == 1 and bases['004']['transfer_in'] == 2
    # 全网基数守恒：有出处转移不改变全网开班基数
    assert snapshot['opening_base']['fleet_base'] == 13


def test_retry_same_sheet_is_idempotent_and_never_double_adjusts(client, run_id, app):
    first = upload(client, run_id).json
    assert first['created'] is True
    again = upload(client, run_id)
    assert again.json['created'] is False and again.json['source_id'] == first['source_id']
    settled = settle(client, run_id, first['source_id']).json
    retry = settle(client, run_id, first['source_id'], settled['id']).json
    assert retry['created'] is False and retry['id'] == settled['id']
    with app.extensions['shift_store'].connect() as conn:
        assert conn.execute('SELECT count(*) FROM closings').fetchone() == (1,)
        assert conn.execute('SELECT count(*) FROM closing_sources').fetchone() == (1,)


def test_stale_closing_guard_uses_optimistic_concurrency(client, run_id):
    source = upload(client, run_id).json['source_id']
    bad = settle(client, run_id, source, 'stale-id')
    assert bad.status_code == 409 and bad.json['code'] == 'STALE_CLOSING'
    ok = settle(client, run_id, source)
    assert ok.status_code == 200 and ok.json['created'] is True


def _publish_new_run(client, run_id, app):
    source = upload(client, run_id).json['source_id']
    old = settle(client, run_id, source).json
    published = client.post('/api/dispatch/publish', json={'expected': run_id, 'legs': []}).json
    new_run = published['id']
    db = app.extensions['shift_store']
    with db.connect() as conn:
        assert conn.execute('SELECT status FROM closings WHERE id=?', (old['id'],)).fetchone()[0] == 'superseded'
        # 原盘点记录与采信记录都还在，没有删除
        assert conn.execute('SELECT count(*) FROM closing_sources').fetchone() == (1,)
        assert conn.execute('SELECT count(*) FROM closings').fetchone() == (1,)
        assert conn.execute('SELECT closing_id FROM active_closing').fetchone() is None
    assert client.get(f'/api/closing/{old["id"]}').status_code == 200
    # 旧结论已失效：active 指针清空，前端能据此要求重算
    assert client.get('/api/closing').json['state'] is None
    return old, new_run, source


def test_publishing_plan_supersedes_old_closing_but_keeps_all_records(client, run_id, app):
    _publish_new_run(client, run_id, app)


def test_recompute_after_plan_change_archives_new_version_and_preserves_history(client, run_id, app):
    old, new_run, source = _publish_new_run(client, run_id, app)
    # 新 run 期末 003=2，原车辆册 003 只有 1 辆；补一辆在册车使车辆册与新勾稽对齐
    book = open(BOOK, encoding='utf-8').read().replace(
        'B4001,004,', 'B3002,003,docked,复算新增在册\nB4001,004,', 1)
    path = tmp_book(book)
    up = upload(client, new_run, book_path=path)
    assert up.status_code == 200
    new_close = settle(client, new_run, up.json['source_id']).json
    assert new_close['id'] != old['id'] and new_close['created'] is True
    db = app.extensions['shift_store']
    with db.connect() as conn:
        rows = conn.execute('SELECT id, status FROM closings ORDER BY created_at').fetchall()
        assert len(rows) == 2 and rows[0][1] == 'superseded' and rows[1][1] == 'active'
        # 两份盘点/车辆册来源都保留
        assert conn.execute('SELECT count(*) FROM closing_sources').fetchone() == (2,)
    history = client.get('/api/closing').json['history']
    assert [row['status'] for row in history] == ['active', 'superseded']


def test_concurrent_equal_close_yields_single_record(app, run_id):
    client = app.test_client()
    source = upload(client, run_id).json['source_id']

    def submit(_):
        return settle(app.test_client(), run_id, source).json
    with ThreadPoolExecutor(2) as workers:
        one, two = workers.map(submit, range(2))
    assert one['id'] == two['id'] and sorted([one['created'], two['created']]) == [False, True]
    with app.extensions['shift_store'].connect() as conn:
        assert conn.execute('SELECT count(*) FROM closings').fetchone() == (1,)


def test_book_mismatched_with_run_closing_is_rejected(client, run_id, tmp_path):
    bad_book = tmp_path / 'book.csv'
    bad_book.write_text(open(BOOK, encoding='utf-8').read().replace('B2010,001', 'B2010,002'),
                        encoding='utf-8')
    response = upload(client, run_id, book_path=str(bad_book))
    assert response.status_code == 422 and response.json['code'] == 'BIKE_BOOK_GAP'
    assert response.json['details']['station_gaps']


def test_unknown_sheet_and_closing_return_404(client, run_id):
    missing = client.post('/api/closing/preview', json={'run': run_id, 'source_id': 'nope'})
    assert missing.status_code == 404 and missing.json['code'] == 'COUNT_SHEET_NOT_FOUND'
    assert client.get('/api/closing/nope').status_code == 404


def test_bad_request_shapes_are_rejected(client, run_id):
    no_file = client.post('/api/closing/sheets', data={'expected_run': run_id})
    assert no_file.status_code == 422 and no_file.json['code'] == 'UPLOAD_REQUIRED'
    bad_close = client.post('/api/closing/close', json={'expected_run': run_id})
    assert bad_close.status_code == 422 and bad_close.json['code'] == 'BAD_REQUEST'


def test_stored_snapshot_carries_input_digests_and_full_evidence(client, run_id):
    source = upload(client, run_id).json
    settled = settle(client, run_id, source['source_id']).json
    snapshot = client.get(f'/api/closing/{settled["id"]}').json
    count_raw = open(COUNT, 'rb').read()
    assert snapshot['manifest']['inputs']['count_sheet']['sha256'] == digest(count_raw)
    # 采信与隔离记录都在归档里，可逐辆追溯到源行
    assert snapshot['count_sheet']['excluded'] and snapshot['dispositions']
    assert all('evidence' in row and 'line' in row for row in snapshot['dispositions'])
