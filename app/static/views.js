import { localInstant } from './axis.js';

export const $ = (id) => document.getElementById(id);
const currency = new Intl.NumberFormat('zh-CN', {style: 'currency', currency: 'CNY'});
const money = (cents) => currency.format(cents / 100);
const instant = (stamp) => stamp ? localInstant(stamp).replace('T', ' ') : '—';
const states = {
  ACTIVE: '生效', SUPERSEDED: '已替换', OUTSIDE_CUT: '截面外', ACTIVE_VERSION: '生效',
  APPLIED: '已入账', PENDING: '待补', QUARANTINED: '隔离', INACTIVE: '期末无有效版本',
};
const bucketNames = {current: '未逾期', d01_30: '1–30 天', d31_60: '31–60 天', d61_90: '61–90 天', d91_plus: '91 天以上'};
const kindNames = {VERSION: '版本', PAYMENT: '收款', REVERSAL: '冲销'};

function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined && text !== null) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}

const cell = (value, cls) => ({value, cls});
const number = (value) => cell(value, 'num');
const cash = (value) => number(money(value));

function table(host, columns, rows, empty = '此切点暂无记录') {
  if (rows.length === 0) {
    $(host).replaceChildren(node('p', empty, 'empty'));
    return;
  }
  const ledger = node('table', null, 'ledger');
  const head = ledger.createTHead().insertRow();
  columns.forEach((column) => {
    const [title, cls] = Array.isArray(column) ? column : [column, null];
    const th = node('th', title, cls);
    th.scope = 'col';
    head.append(th);
  });
  const body = ledger.createTBody();
  rows.forEach((values) => {
    const tr = body.insertRow();
    values.forEach((item) => {
      const td = tr.insertCell();
      if (item instanceof Node) {
        td.append(item);
      } else {
        const {value, cls} = item && typeof item === 'object' ? item : {value: item};
        td.textContent = value ?? '—';
        td.title = td.textContent;
        if (cls) td.className = cls;
      }
    });
  });
  $(host).replaceChildren(ledger);
}

function definitions(items) {
  const list = node('dl', null, 'info-list');
  for (const [label, value] of items) {
    const row = node('div');
    row.append(node('dt', label), node('dd', value ?? '—', 'mono'));
    list.append(row);
  }
  return list;
}

function evidenceSection(title, entries, raw) {
  const section = node('section');
  section.append(node('h3', title), definitions(entries));
  if (raw) section.append(node('pre', JSON.stringify(raw, null, 2)));
  return section;
}

function eventSection(title, data, extra = []) {
  return evidenceSection(title, [['事件', data.event_id], ['发票 / 客户', `${data.invoice_id} / ${data.customer_id}`],
    ['变更类型', kindNames[data.kind]], ['来源行', data.line], ['有效日', data.effective],
    ['知悉时间', instant(data.known)], ['到期日', data.due], ['金额', money(data.amount_cents)],
    ['引用事件', data.ref || '—'], ['备注', data.note || '—'], ...extra]);
}

function evidence(title, render, label = '查看') {
  const button = node('button', label, 'evidence-btn');
  button.type = 'button';
  button.addEventListener('click', () => {
    $('details-title').textContent = title;
    $('details-body').replaceChildren(...render());
    $('details').showModal();
  });
  return button;
}

function reasonEvidence(row) {
  return evidence(row.code, () => {
    const extra = [['原因', row.message]];
    if (row.available_cents !== undefined) extra.push(['可用余额', money(row.available_cents)]);
    if (!row.event) return [evidenceSection('收件原值', [['来源行', row.line], ['事件编号', row.event_id], ...extra], row.raw)];
    return [eventSection('变更凭证', row.event, extra),
      ...(row.target ? [eventSection('引用版本', row.target)] : []),
      ...(row.related ?? []).map((item) => eventSection('关联版本', item))];
  });
}

function issues(host, rows) {
  table(host, [['行', 'num'], '事件', '原因码', '原因', '证据'], rows.map((row) => [
    number(row.event?.line ?? row.line), row.event?.event_id ?? row.event_id, cell(row.code, 'mono warn'), row.message, reasonEvidence(row),
  ]), '暂无待补或隔离记录');
}

export function intake(data) {
  const totals = data.counts;
  $('intake-summary').replaceChildren(definitions([['数据摘要', data.source_id.slice(0, 12)], ['编码', data.encoding],
    ['输入记录', totals.input], ['采信记录', totals.accepted], ['隔离', totals.quarantine],
    ['重复', totals.duplicates], ['空白', totals.blank], ['重复表头', totals.repeated_headers]]));
  issues('intake-issues', [...data.quarantine, ...data.duplicates].sort((left, right) => left.line - right.line));
}

export function snapshot(data, agingData) {
  const totals = data.totals;
  $('k-balance').textContent = money(totals.balance_cents);
  $('k-overdue').textContent = money(agingData.overdue_cents);
  $('k-receipts').textContent = money(totals.receipts_cents);
  $('k-reversed').textContent = money(totals.reversed_cents);
  $('k-issues').textContent = `${data.pending.length} / ${data.quarantine.length}`;
  $('invoice-count').textContent = `${data.invoices.length} 笔`;
  table('invoice-list', ['发票 / 客户', '使用版本', '到期日', ['应收余额', 'num'], '凭证'], data.invoices.map((row) => [
    cell(`${row.invoice_id} / ${row.customer_id}`, 'mono'), cell(row.version.event_id, 'mono'), row.due, cash(row.balance_cents),
    evidence(row.invoice_id, () => [eventSection('生效版本', row.version, [['余额', money(row.balance_cents)]]),
      ...row.movements.map((move) => eventSection('冲减凭证', move.event, [['原票行', move.target.line], ['当日版本行', move.at_event.line]]))]),
  ]));
  issues('cut-issues', [...data.pending, ...data.quarantine]);
  const max = Math.max(1, ...agingData.buckets.map((row) => row.balance_cents));
  $('age-bars').replaceChildren(...agingData.buckets.map((row) => {
    const line = node('div', null, 'age-row');
    const progress = node('progress', null, 'age-meter');
    progress.max = max;
    progress.value = row.balance_cents;
    progress.setAttribute('aria-label', row.label);
    line.append(node('span', row.label), node('span', money(row.balance_cents), 'num'), progress);
    return line;
  }));
}

export function versions(data) {
  table('versions-list', ['发票', '版本', '有效区间', '知悉起点', '状态', '凭证'], data.versions.map((row) => [
    cell(row.invoice_id, 'mono'), cell(row.event_id, 'mono'), `${row.effective} → ${row.valid_to ?? '无终点'}`,
    instant(row.known), states[row.state], evidence(row.event_id, () => [eventSection('版本凭证', row,
      [['知悉终点', instant(row.known_to)], ['替换事件', row.replaced_by]])]),
  ]));
  issues('versions-issues', data.quarantine);
}

export function aging(data) {
  const filter = $('bucket-filter').value;
  table('aging-summary', ['账龄桶', ['笔数', 'num'], ['应收余额', 'num']], [
    ...data.buckets.map((row) => [row.label, number(row.count), cash(row.balance_cents)]),
    ['桶合计', number(data.invoices.length), cash(data.total_cents)],
    ['截面差额', null, cash(data.difference_cents)],
    ['已结清', number(data.cleared), cash(0)],
  ]);
  const rows = data.invoices.filter((row) => !filter || row.bucket === filter);
  table('aging-list', ['发票', '客户', '到期日', ['逾期天数', 'num'], '账龄', ['应收余额', 'num'], ['来源行', 'num']], rows.map((row) => [
    cell(row.invoice_id, 'mono'), cell(row.customer_id, 'mono'), row.due, number(row.days_overdue), bucketNames[row.bucket],
    cash(row.balance_cents), number(row.version_line),
  ]));
}

export function backfill(data) {
  $('backfill-summary').replaceChildren(definitions([['有效日', data.before_cut.effective], ['原知悉截止', instant(data.before_cut.known)],
    ['新知悉截止', instant(data.after_cut.known)], ['入账变化', money(data.totals.face_cents)], ['收款变化', money(data.totals.receipts_cents)],
    ['冲销变化', money(data.totals.reversed_cents)], ['余额变动', money(data.totals.balance_cents)]]));
  table('backfill-list', ['事件', ['行', 'num'], '原状态', '新状态', '原因码', '凭证'], data.changes.map((row) => [
    cell(row.event.event_id, 'mono'), number(row.event.line), states[row.before?.state], states[row.after?.state], row.after?.code,
    evidence(row.event.event_id, () => [eventSection('回补凭证', row.event, [['原状态', states[row.before?.state]],
      ['新状态', states[row.after?.state]], ['原因', row.after?.message], ['原引用行', row.before?.target?.line], ['新引用行', row.after?.target?.line]])]),
  ]), '两次知悉截止之间暂无回补');
}

export function bridge(data) {
  const totals = data.totals;
  $('bridge-state').textContent = data.status === 'MATCHED' ? '已对平' : '存在差额';
  $('bridge-state').className = data.status === 'MATCHED' ? 'subtle good' : 'subtle warn';
  $('bridge-summary').replaceChildren(definitions([['期初', money(totals.opening_cents)], ['＋ 入账净变动', money(totals.posted_cents)],
    ['－ 收款净变动', money(totals.collected_cents)], ['－ 冲销净变动', money(totals.reversed_cents)],
    ['＝ 期末', money(totals.closing_cents)], ['期间差额', money(totals.difference_cents)], ['迁移净额', money(data.migration_net_cents)]]));
  table('bridge-buckets', ['账龄桶', ['期初', 'num'], ['迟报', 'num'], ['迁移', 'num'], ['本期', 'num'], ['期末', 'num'], ['差额', 'num']],
    data.buckets.map((row) => [row.label, cash(row.opening_cents), cash(row.late_cents), cash(row.migration_cents),
      cash(row.period_cents), cash(row.closing_cents), cash(row.difference_cents)]));
  table('bridge-list', ['发票', ['期初', 'num'], ['入账净变动', 'num'], ['收款净变动', 'num'], ['冲销净变动', 'num'], ['期末', 'num'], '证据'],
    data.invoices.map((row) => {
      const total = row.reconciliation;
      return [cell(row.invoice_id, 'mono'), cash(total.opening_cents), cash(total.posted_cents), cash(total.collected_cents),
        cash(total.reversed_cents), cash(total.closing_cents), evidence(row.invoice_id, () => [[
          '期初', row.opening, row.bucket_path.opening], ['回补后期初', row.restated, row.bucket_path.restated],
        ['期末', row.closing, row.bucket_path.closing]].map(([title, record, band]) => record ? eventSection(title, record.version, [
          ['账龄桶', bucketNames[band]], ['收款', money(record.receipts_cents)], ['冲销', money(record.reversed_cents)],
          ['应收余额', money(record.balance_cents)], ['冲减凭证行', record.movements.map((move) => move.event.line).join('、')],
        ]) : evidenceSection(title, [['有效版本', '—']])) )];
    }));
}

export function history(runs, selected, pick) {
  table('archive-list', ['批次', '生成时间', '规则版本', ['耗时 ms', 'num']], runs.map((run) => {
    const button = node('button', run.run_id.slice(0, 12), 'evidence-btn mono');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(selected?.run_id === run.run_id));
    button.addEventListener('click', () => pick(run));
    return [button, instant(run.generated_at), run.rule_version, number(run.elapsed_ms)];
  }), '请先归档当前切点');
  if (selected) $('archive-detail').replaceChildren(definitions([['批次号', selected.run_id],
    ['源文件 SHA-256', selected.source_id], ['结果 SHA-256', selected.result_digest],
    ['生成时间', instant(selected.generated_at)], ['规则版本', selected.rule_version], ['耗时 ms', selected.elapsed_ms]]));
  else $('archive-detail').replaceChildren(node('p', '请选择归档批次', 'empty'));
}

const restateClasses = {LATE_REPORT: '补报', CORRECTION: '更正'};
const restatePolicies = {ARCHIVE_PLUS_SUPPLEMENTS: '原归档叠加补报（采纳）', LATEST_INTAKE_ONLY: '最新收件整体重算（未采纳）'};

function restateEventEvidence(row) {
  return evidence(row.event.event_id, () => [eventSection('补报凭证', row.event,
    [['来路分类', restateClasses[row.class]], ['收件序号', row.source_seq],
     ['收件来源', row.source_id], ['物理行', row.source_line], ['来源角色', row.source_role]])]);
}

function restateIssues(host, rows) {
  table(host, ['事件', '原因码', '原因', '收件序号', ['物理行', 'num'], '证据'], rows.map((row) => [
    cell(row.event_id, 'mono'), cell(row.code, 'mono warn'), row.message,
    number(row.source_seq), number(row.source_line ?? row.line),
    evidence(row.code, () => [evidenceSection('隔离 / 重复凭证',
      [['事件编号', row.event_id], ['原因', row.message], ['收件来源', row.source_id],
       ['收件序号', row.source_seq], ['物理行', row.source_line ?? row.line],
       ...(row.related ? [['同号已采信事件', row.related.map((item) => item.event_id).join('、')]] : [])], row.raw)])])),
    '暂无跨来源重复或隔离记录');
}

export function restatement(data, runs, selectedId, select) {
  table('restate-list', ['重述批次', '原结账批次', '生成时间', '规则版本'], runs.map((run) => {
    const button = node('button', run.restate_id.slice(0, 12), 'evidence-btn mono');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(selectedId === run.restate_id));
    button.addEventListener('click', () => select(run.restate_id));
    return [button, cell(run.parent_run_id.slice(0, 12), 'mono'), instant(run.generated_at), run.rule_version];
  }), '尚无重述批次');
  if (!data) {
    $('restate-summary').replaceChildren(node('p', '请先生成重述批次', 'empty'));
    $('restate-evidence').replaceChildren(node('p', '请先生成重述批次', 'empty'));
    $('restate-balance').textContent = '';
    return;
  }
  const result = data.result;
  const recon = result.attribution.balance_reconciliation;
  const alt = result.alternatives[0];
  $('restate-balance').textContent = `重述批次 ${data.manifest.restate_id.slice(0, 12)} · 重述知悉截止 ${result.restatement.restate_period.closing.known}`;
  $('restate-summary').replaceChildren(definitions([
    ['原结账批次', result.parent.run_id.slice(0, 12)],
    ['原结账余额', money(recon.original_closing_cents)],
    ['＋ 补报净影响', money(recon.late_balance_cents)],
    ['＋ 更正净影响', money(recon.correction_balance_cents)],
    ['＝ 重述后余额', money(recon.restated_closing_cents)],
    ['勾稽差额', money(recon.difference_cents)],
    ['采纳口径', restatePolicies[result.policy]],
    ['未采纳口径余额', money(alt.closing_totals.balance_cents)],
    ['口径差异（未采纳−采纳）', money(alt.difference_vs_adopted_cents)],
  ]));
  const rows = result.supplement_evidence.accepted.map((row) => [
    cell(row.event.event_id, 'mono'), cell(row.event.invoice_id, 'mono'), kindNames[row.event.kind],
    restateClasses[row.class], number(row.source_seq), number(row.source_line),
    money(row.event.amount_cents), restateEventEvidence(row)]);
  table('restate-evidence', ['事件', '发票', '类型', '来路', ['收件序号', 'num'], ['物理行', 'num'], ['金额', 'num'], '证据'],
    rows, '补充来源中暂无被采信的凭证');
  const host = $('restate-evidence');
  const old = $('restate-evidence-issues');
  if (old) old.remove();
  host.append(node('h3', '跨来源重复与隔离', ''));
  const wrap = node('div');
  wrap.id = 'restate-evidence-issues';
  host.append(wrap);
  restateIssues(wrap, [...result.supplement_evidence.duplicates, ...result.supplement_evidence.quarantine]);
}
