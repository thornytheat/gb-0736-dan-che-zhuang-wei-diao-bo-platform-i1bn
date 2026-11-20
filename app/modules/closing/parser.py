from app.clock import instant
from app.errors import DomainError
from app.modules.intake.reader import records

COUNT_FIELDS = ('bike_id', 'station', 'counted_at', 'note')
BOOK_FIELDS = ('bike_id', 'home_station', 'status', 'note')
MISSING = {'', '-', 'n/a', 'null', '未知'}


def parse_count_sheet(raw):
    rows, encoding = records(raw, COUNT_FIELDS)
    accepted, excluded = [], []
    for row in rows:
        if row['code']:
            excluded.append({'line': row['line'], 'raw': row['raw'], 'code': row['code']})
            continue
        cells = row['cells']
        if any(cells[field].lower() in MISSING for field in COUNT_FIELDS[:-1]):
            excluded.append({'line': row['line'], 'raw': row['raw'], 'code': 'MISSING_FIELD'})
            continue
        try:
            at = instant(cells['counted_at'])
        except DomainError:
            excluded.append({'line': row['line'], 'raw': row['raw'], 'code': 'COUNT_TIME_INVALID'})
            continue
        accepted.append({'bike_id': cells['bike_id'], 'station': cells['station'],
                         'at': at, 'note': cells['note'], 'line': row['line'], 'raw': row['raw']})
    return accepted, excluded, encoding, len(rows)


def parse_book(raw, shift):
    rows, encoding = records(raw, BOOK_FIELDS)
    accepted, excluded = [], []
    for row in rows:
        if row['code']:
            excluded.append({'line': row['line'], 'raw': row['raw'], 'code': row['code']})
            continue
        cells = row['cells']
        if any(cells[field].lower() in MISSING for field in BOOK_FIELDS[:-1]):
            excluded.append({'line': row['line'], 'raw': row['raw'], 'code': 'MISSING_FIELD'})
            continue
        if cells['home_station'] not in shift.stations:
            excluded.append({'line': row['line'], 'raw': row['raw'], 'code': 'UNKNOWN_BIKE_STATION'})
            continue
        accepted.append({'bike_id': cells['bike_id'], 'home_station': cells['home_station'],
                         'status': cells['status'], 'note': cells['note'],
                         'line': row['line'], 'raw': row['raw']})
    return accepted, excluded, encoding, len(rows)


def enforce_window(counts, shift):
    """点数时点必须落在班次窗口内；违规行隔离而非整单拒收。"""
    kept, excluded = [], []
    for row in counts:
        if shift.start <= row['at'] < shift.end:
            kept.append(row)
        else:
            excluded.append({'line': row['line'], 'raw': row['raw'], 'code': 'COUNT_OUTSIDE_WINDOW'})
    return kept, excluded


def dedupe_counts(counts):
    """同一车辆在同一站点重复上报：全部隔离，避免重复调账。
    跨站重复不在此处理，留给关账引擎的双站冲突仲裁。"""
    grouped = {}
    for row in counts:
        grouped.setdefault((row['bike_id'], row['station']), []).append(row)
    accepted, excluded = [], []
    for (bike_id, station), group in grouped.items():
        if len(group) > 1:
            for row in group:
                excluded.append({'line': row['line'], 'raw': row['raw'], 'code': 'COUNT_DUPLICATE_BIKE'})
        else:
            accepted.append(group[0])
    accepted.sort(key=lambda row: (row['line'], row['bike_id']))
    excluded.sort(key=lambda row: row['line'])
    return accepted, excluded
