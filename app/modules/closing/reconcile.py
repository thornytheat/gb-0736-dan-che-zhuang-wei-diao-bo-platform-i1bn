from collections import Counter, defaultdict

from app.clock import instant, stamp
from app.errors import DomainError
from app.reasons import BASE_ADJUSTED, SUSPENDED
from .parser import dedupe_counts, enforce_window, parse_book, parse_count_sheet


def closed_segments(run):
    """从运行快照的容量版本中取关站区间（slots=0），作为关站暂存的出处证据。"""
    return [{'station': row['station'], 'start': row['start'], 'end': row['end'],
             'start_at': instant(row['start']), 'end_at': instant(row['end']),
             'revision': row['revision'], 'line': row['line'], 'note': row['note']}
            for row in run['capacity']['segments'] if row['slots'] == 0]


def is_closed(segments, station, at):
    return any(seg['station'] == station and seg['start_at'] <= at < seg['end_at']
               for seg in segments)


def book_closing_totals(run):
    return {row['station']: row['closing'] for row in run['inventory']['plan']['stations']}


def validate_book(book_rows, book_excluded, run, shift):
    """车辆册逐站数量必须与运行快照的期末勾稽数一致，否则拒绝关账，防止拿错账。"""
    grouped = defaultdict(list)
    for row in book_rows:
        grouped[row['bike_id']].append(row)
    duplicates = [row for group in grouped.values() if len(group) > 1 for row in group]
    for row in duplicates:
        book_excluded.append({'line': row['line'], 'raw': row['raw'], 'code': 'COUNT_DUPLICATE_BIKE'})
    unique = [group[0] for group in grouped.values() if len(group) == 1]
    totals = book_closing_totals(run)
    by_station = Counter(row['home_station'] for row in unique)
    gaps = {station: by_station.get(station, 0) - totals.get(station, 0)
            for station in shift.stations if by_station.get(station, 0) != totals.get(station, 0)}
    if gaps or duplicates:
        raise DomainError('BIKE_BOOK_GAP', station_gaps=gaps,
                          book_count=len(unique), expected=dict(totals))
    return {row['bike_id']: row for row in unique}


def resolve_conflict(bike_id, claims, book, closures):
    """多站上报同一辆车：归属站在开且上报则归属站优先；否则按上报时点、站号确定性仲裁。"""
    home = book.get(bike_id, {}).get('home_station')

    def sort_key(row):
        home_priority = 0 if (home and row['station'] == home
                              and not is_closed(closures, home, row['at'])) else 1
        return (home_priority, row['at'], row['station'], row['line'])

    ordered = sorted(claims, key=sort_key)
    winner, losers = ordered[0], ordered[1:]
    return winner, losers


def reconcile(count_raw, book_raw, run, shift, started, manifest_builder, source_id=None):
    count_rows, count_excluded, count_encoding, count_input = parse_count_sheet(count_raw)
    book_rows, book_excluded, book_encoding, book_input = parse_book(book_raw, shift)
    in_window, window_excluded = enforce_window(count_rows, shift)
    count_rows, duplicate_excluded = dedupe_counts(in_window)
    count_excluded.extend(window_excluded + duplicate_excluded)
    count_excluded.sort(key=lambda row: row['line'])

    book = validate_book(book_rows, book_excluded, run, shift)
    closures = closed_segments(run)

    claims = defaultdict(list)
    for row in count_rows:
        claims[row['bike_id']].append(row)

    accepted_counts, conflict_rejects = {}, []
    for bike_id, group in claims.items():
        if len(group) == 1:
            accepted_counts[bike_id] = group[0]
            continue
        winner, losers = resolve_conflict(bike_id, group, book, closures)
        accepted_counts[bike_id] = winner
        for row in losers:
            conflict_rejects.append({'bike_id': bike_id, 'line': row['line'], 'raw': row['raw'],
                                     'code': 'CONFLICT_REJECT', 'station': row['station'],
                                     'winner_station': winner['station'], 'winner_line': winner['line']})

    dispositions = []

    def add(bike_id, code, counted_station, evidence, source_line):
        home = book.get(bike_id, {}).get('home_station')
        dispositions.append({'bike_id': bike_id, 'code': code, 'book_station': home,
                             'counted_station': counted_station, 'line': source_line,
                             'evidence': evidence,
                             'base_effect': 1 if code in BASE_ADJUSTED else 0,
                             'suspended': code in SUSPENDED})

    for bike_id, row in accepted_counts.items():
        station, at, line = row['station'], row['at'], row['line']
        if bike_id not in book:
            add(bike_id, 'SURPLUS_SUSPENSE', station,
                {'reason': '现场盘到账面未登记车辆，无来历凭证', 'counted_at': stamp(at)}, line)
            continue
        home = book[bike_id]['home_station']
        if station == home:
            add(bike_id, 'MATCHED', station, {'reason': '现场与账面归属站一致'}, line)
        elif is_closed(closures, home, at):
            full = next(seg for seg in closures
                        if seg['station'] == home and seg['start_at'] <= at < seg['end_at'])
            seg = {key: full[key] for key in ('station', 'start', 'end', 'revision', 'line', 'note')}
            add(bike_id, 'STAGED_CLOSED', station,
                {'reason': f"归属站 {home} 关站检修，车辆暂存相邻站 {station}", 'closure': seg,
                 'counted_at': stamp(at)}, line)
        else:
            winner_line = line
            loser_lines = [item['line'] for item in conflict_rejects if item['bike_id'] == bike_id]
            add(bike_id, 'CONFIRMED_TRANSFER', station,
                {'reason': f"双站上报冲突，采信 {station} 上报", 'book_station': home,
                 'accepted_line': winner_line, 'rejected_lines': loser_lines,
                 'counted_at': stamp(at)}, line)

    for bike_id, row in book.items():
        if bike_id not in accepted_counts:
            add(bike_id, 'SHORTAGE_SUSPENSE', None,
                {'reason': '账面在册但现场未盘到，差异无出处', 'book_station': row['home_station']}, None)

    for item in conflict_rejects:
        dispositions.append({'bike_id': item['bike_id'], 'code': 'CONFLICT_REJECT',
                             'book_station': book.get(item['bike_id'], {}).get('home_station'),
                             'counted_station': item['station'], 'line': item['line'],
                             'evidence': {'reason': '冲突仲裁败方，隔离不调账',
                                          'winner_station': item['winner_station'],
                                          'winner_line': item['winner_line']},
                             'base_effect': 0, 'suspended': False})

    order = {'STAGED_CLOSED': 0, 'CONFIRMED_TRANSFER': 1, 'CONFLICT_REJECT': 2,
             'SURPLUS_SUSPENSE': 3, 'SHORTAGE_SUSPENSE': 4, 'MATCHED': 5}
    dispositions.sort(key=lambda row: (order[row['code']], row['bike_id'], row['line'] or 0))

    book_totals = book_closing_totals(run)
    physical = Counter(row['station'] for row in accepted_counts.values())
    explain_in = Counter()
    explain_out = Counter()
    for row in dispositions:
        if row['code'] == 'MATCHED' or row['code'] == 'CONFLICT_REJECT':
            continue
        if row['code'] in BASE_ADJUSTED:
            explain_out[row['book_station']] += 1
            explain_in[row['counted_station']] += 1

    station_bases = []
    for station in shift.stations:
        book_total = book_totals.get(station, 0)
        adjustment = explain_in.get(station, 0) - explain_out.get(station, 0)
        station_bases.append({
            'station': station, 'name': shift.stations[station]['name'],
            'book_closing': book_total, 'physical_count': physical.get(station, 0),
            'transfer_in': explain_in.get(station, 0), 'transfer_out': explain_out.get(station, 0),
            'base_adjustment': adjustment, 'opening_base': book_total + adjustment,
            'physical_gap': physical.get(station, 0) - book_total,
        })

    code_counts = Counter(row['code'] for row in dispositions)
    suspense_held = sum(1 for row in dispositions if row['suspended'])
    fleet_book = sum(book_totals.values())
    fleet_physical = sum(physical.values())
    opening_base = sum(row['opening_base'] for row in station_bases)

    payload = {
        'manifest': manifest_builder(count_raw, book_raw, started),
        'run_id': run['manifest']['id'],
        'source_id': source_id,
        'shift': {'name': shift.name, 'start': stamp(shift.start), 'end': stamp(shift.end),
                  'stations': shift.stations},
        'count_sheet': {'encoding': count_encoding, 'input_rows': count_input,
                        'accepted': len(count_rows),
                        'excluded': [dict(row, source='count_sheet') for row in count_excluded]},
        'book_sheet': {'encoding': book_encoding, 'input_rows': book_input,
                       'accepted': len(book),
                       'excluded': [dict(row, source='book_sheet') for row in book_excluded]},
        'closures': [{key: seg[key] for key in ('station', 'start', 'end', 'revision', 'line', 'note')}
                     for seg in closures],
        'dispositions': dispositions,
        'station_bases': station_bases,
        'opening_base': {
            'fleet_book': fleet_book, 'fleet_physical': fleet_physical,
            'fleet_base': opening_base, 'road_end': run['inventory']['plan']['road_end'],
            'suspense_held': suspense_held,
            'unexplained_gap': fleet_physical - fleet_book,
            'adjusted_bikes': code_counts.get('STAGED_CLOSED', 0) + code_counts.get('CONFIRMED_TRANSFER', 0),
        },
        'disposition_counts': dict(code_counts),
    }
    return payload
