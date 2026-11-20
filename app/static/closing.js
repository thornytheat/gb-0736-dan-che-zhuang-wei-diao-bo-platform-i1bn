import {$, api, clock, date, kpis, label, node, notice, post, signed, table, time} from './view.js';

let preview = null;
let sourceId = null;
let closingState = null;
let activeRun = null;
let busy = false;

const stationName = shift => code => shift.stations[code] ? `${shift.stations[code].name} · ${code}` : code;

function dispositionLabel(code) {
  // 处置类型标签优先取 DISPOSITIONS（通过内联 JSON 暴露），失败回退原因码
  const labels = window.CLOSING_LABELS || {};
  return labels[code] || label(code);
}

function renderSummary(snapshot, shift) {
  const base = snapshot.opening_base;
  kpis([['账面期末', base.fleet_book, '辆'], ['现场实盘', base.fleet_physical, '辆'],
    ['开班基数', base.fleet_base, '辆', 'ok'], ['有出处调整', base.adjusted_bikes, '辆'],
    ['挂起待查', base.suspense_held, '辆', 'warn'], ['在骑池', base.road_end, '辆']]);
  const rows = snapshot.station_bases.map(row =>
    [stationName(shift)(row.station), row.book_closing, row.physical_count,
     signed(row.physical_gap), `+${row.transfer_in}/-${row.transfer_out}`,
     signed(row.base_adjustment), row.opening_base]);
  table('opening-bases', ['站点', '账面期末', '现场实盘', '盘点差', '调入/调出', '基数调整', '开班基数'],
        rows, '暂无站点');
  const explain = document.createElement('p');
  explain.className = 'subtle';
  explain.textContent = `开班基数只纳入有出处的关站暂存与冲突采信（${base.adjusted_bikes} 辆）；`
    + `${base.suspense_held} 辆无出处差车挂起，不凭空调平。全网未解释差额 ${signed(base.unexplained_gap)} 辆，在骑池 ${base.road_end} 辆另计。`;
  $('opening-bases').prepend(explain);
}

function renderDispositions(snapshot, shift) {
  const filter = $('closing-filter').value;
  let rows = snapshot.dispositions;
  if (filter === 'diff') rows = rows.filter(row => row.code !== 'MATCHED' && row.code !== 'CONFLICT_REJECT');
  if (filter === 'suspended') rows = rows.filter(row => row.suspended);
  if (filter === 'adjusted') rows = rows.filter(row => row.base_effect);
  table('closing-dispositions',
    ['车辆', '处置', '账面站', '现场站', '基数', '出处/证据', '源行'],
    rows.map(row => [row.bike_id, dispositionLabel(row.code),
      row.book_station ? stationName(shift)(row.book_station) : '未登记',
      row.counted_station ? stationName(shift)(row.counted_station) : '未盘到',
      row.base_effect ? '+1' : '—',
      row.evidence.reason + (row.evidence.closure ? `（关站 ${time(row.evidence.closure.start)}–${time(row.evidence.closure.end)}）` : ''),
      row.line ?? '—']),
    '该筛选下没有车辆');
}

function renderExcluded(snapshot) {
  const all = [...snapshot.count_sheet.excluded, ...snapshot.book_sheet.excluded];
  table('closing-excluded', ['来源', '源行', '原因码', '判定', '原始字段'], all.map(row =>
    [row.source === 'book_sheet' ? '车辆册' : '点数单', row.line, row.code, label(row.code),
     (row.raw || []).join(' ｜ ')]), '没有隔离行');
}

function renderHistory() {
  if (!closingState) return;
  table('closing-history', ['关账编号', '绑定班次', '生成时间', '开班基数', '挂起', '状态'],
    closingState.history.map(row => [row.id.slice(0, 12), row.run_id.slice(0, 12),
      `${date(row.created_at)} ${clock(row.created_at)}`, row.fleet_base, row.suspense_held,
      row.active ? '生效中' : row.status === 'superseded' ? '已失效待重算' : row.status]),
    '还没有关账版本');
}

export async function mount(shiftMeta) {
  busy = false;
  preview = null;
  sourceId = null;
  activeRun = shiftMeta.manifest.id;
  const state = await api('/api/closing');
  closingState = state;
  const statusEl = $('closing-status');
  if (state.state) {
    statusEl.textContent = state.state.status === 'active'
      ? `当前生效关账 ${state.state.id.slice(0, 12)}` : '旧结论已失效，需重算';
  } else {
    statusEl.textContent = activeRun === state.active_run ? '尚未关账' : '班次已变更，需重新关账';
  }
  const snapshot = state.state ? state.state.payload : null;
  if (snapshot) {
    renderSummary(snapshot, shiftMeta.shift);
    renderDispositions(snapshot, shiftMeta.shift);
    renderExcluded(snapshot);
  } else {
    $('opening-bases').replaceChildren(node('p', '上传点数单后生成关账结论', 'empty'));
    $('closing-dispositions').replaceChildren(node('p', '暂无采信记录', 'empty'));
    $('closing-excluded').replaceChildren(node('p', '暂无隔离行', 'empty'));
  }
  renderHistory();
  $('closing-commit').disabled = !preview;
}

export function bind(shiftMetaLoader) {
  $('closing-filter').addEventListener('change', () => {
    const snapshot = preview || (closingState?.state?.payload);
    if (snapshot) renderDispositions(snapshot, shiftMetaLoader().shift);
  });

  $('closing-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    const meta = shiftMetaLoader();
    const form = new FormData(event.currentTarget);
    form.set('expected_run', meta.manifest.id);
    busy = true;
    $('closing-commit').disabled = true;
    notice('正在解析点数单并试算…');
    try {
      const result = await api('/api/closing/sheets', {method: 'POST', body: form});
      preview = result.preview;
      sourceId = result.source_id;
      activeRun = meta.manifest.id;
      renderSummary(preview, meta.shift);
      renderDispositions(preview, meta.shift);
      renderExcluded(preview);
      $('closing-commit').disabled = false;
      notice(result.created ? '点数单已登记 · 试算完成，可确认关账' : '同一点数单已登记 · 未重复调整');
    } catch (error) {
      notice(error.message || '试算失败', true);
    } finally {
      busy = false;
    }
  });

  $('closing-commit').addEventListener('click', async () => {
    if (busy || !preview) return;
    const meta = shiftMetaLoader();
    busy = true;
    $('closing-commit').disabled = true;
    notice('正在写入关账结论…');
    try {
      const expectedClosing = closingState?.state?.id ?? null;
      const result = await post('/api/closing/close', {
        expected_run: meta.manifest.id, expected_closing: expectedClosing, source_id: sourceId});
      notice(`关账完成 · 开班基数 ${result.fleet_base} 辆，挂起 ${result.suspense_held} 辆`
        + (result.created ? '' : '（同一盘点重试，未重复调整）'));
      await mount(meta);
    } catch (error) {
      notice(error.message || '关账失败', true, error.code === 'STALE_RUN' || error.code === 'STALE_CLOSING');
      $('closing-commit').disabled = false;
    } finally {
      busy = false;
    }
  });
}
