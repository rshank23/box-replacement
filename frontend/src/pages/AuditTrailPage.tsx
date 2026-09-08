import { useMemo, useState } from 'react';

import { useAudit } from '@/api/hooks';
import { Badge, type Tone } from '@/components/Badge';
import { EmptyState, Notice, QueryState } from '@/components/Feedback';
import { downloadCsv, formatDateTime } from '@/lib/format';

const ACTIONS = [
  'UPLOAD',
  'MAP',
  'CLASSIFY',
  'VALIDATE',
  'RETRY',
  'DELETE',
  'CONFIG_CHANGE',
  'SCAN',
  'SAM_REQUEST',
];

const ACTION_TONES: Record<string, Tone> = {
  UPLOAD: 'ok',
  MAP: 'info',
  VALIDATE: 'info',
  CLASSIFY: 'warn',
  SAM_REQUEST: 'warn',
  RETRY: 'warn',
  DELETE: 'danger',
  CONFIG_CHANGE: 'danger',
  SCAN: 'neutral',
};

function summarise(details: Record<string, unknown> | null): string {
  if (!details) return '—';
  const preferred = ['vault_document_id', 'status', 'reason', 'operation', 'result', 'detail', 'missing_fields'];
  for (const key of preferred) {
    const value = details[key];
    if (value !== undefined && value !== null && value !== '') {
      return typeof value === 'object' ? JSON.stringify(value) : String(value);
    }
  }
  return JSON.stringify(details).slice(0, 160);
}

export function AuditTrailPage() {
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [action, setAction] = useState('');
  const [performedBy, setPerformedBy] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);

  const audit = useAudit({
    from: from ? new Date(from).toISOString() : undefined,
    to: to ? new Date(`${to}T23:59:59`).toISOString() : undefined,
    action: action || undefined,
    performed_by: performedBy.trim() || undefined,
  });

  const entries = useMemo(() => audit.data ?? [], [audit.data]);

  function exportCsv() {
    downloadCsv(
      `audit-trail-${new Date().toISOString().slice(0, 10)}.csv`,
      entries.map((entry) => ({
        audit_id: entry.audit_id,
        timestamp: entry.timestamp,
        performed_by: entry.performed_by,
        action: entry.action,
        source_system: entry.source_system ?? '',
        correlation_id: entry.correlation_id ?? '',
        details: JSON.stringify(entry.details ?? {}),
      })),
    );
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Audit Trail</h1>
          <p>
            Append-only record of every action. Updates and deletes are rejected by database triggers, so this view
            is the complete and unaltered history.
          </p>
        </div>
        <div className="btn-row">
          <button type="button" className="btn" onClick={exportCsv} disabled={entries.length === 0}>
            ⬇ Export CSV
          </button>
          <button type="button" className="btn" onClick={() => window.print()} disabled={entries.length === 0}>
            🖨 Export PDF
          </button>
        </div>
      </div>

      <Notice tone="info">
        Records are attributable, contemporaneous and immutable in line with 21 CFR Part 11. Each entry carries the
        correlation id that links an MBox source path to the resulting Vault document.
      </Notice>

      <section className="card">
        <div className="filter-bar">
          <div className="field">
            <label htmlFor="a-from">From</label>
            <input id="a-from" type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="a-to">To</label>
            <input id="a-to" type="date" value={to} onChange={(event) => setTo(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="a-action">Action</label>
            <select id="a-action" value={action} onChange={(event) => setAction(event.target.value)}>
              <option value="">All actions</option>
              {ACTIONS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ flex: 1, minWidth: 200 }}>
            <label htmlFor="a-user">User</label>
            <input
              id="a-user"
              type="text"
              placeholder="e.g. svc-mbox"
              value={performedBy}
              onChange={(event) => setPerformedBy(event.target.value)}
            />
          </div>
        </div>

        <div className="card-body tight">
          <QueryState isLoading={audit.isLoading} error={audit.error}>
            {entries.length === 0 ? (
              <EmptyState icon="🧾" title="No audit entries match these filters" />
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Timestamp</th>
                      <th>User</th>
                      <th>Action</th>
                      <th>Source</th>
                      <th>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map((entry) => (
                      <tr
                        key={entry.audit_id}
                        data-clickable="true"
                        onClick={() => setExpanded(expanded === entry.audit_id ? null : entry.audit_id)}
                      >
                        <td>{formatDateTime(entry.timestamp)}</td>
                        <td>{entry.performed_by}</td>
                        <td>
                          <Badge tone={ACTION_TONES[entry.action] ?? 'neutral'}>{entry.action}</Badge>
                        </td>
                        <td>{entry.source_system ?? '—'}</td>
                        <td className={expanded === entry.audit_id ? 'mono' : 'truncate'}>
                          {expanded === entry.audit_id
                            ? JSON.stringify(entry.details ?? {}, null, 2)
                            : summarise(entry.details)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </QueryState>
        </div>
      </section>
    </>
  );
}
