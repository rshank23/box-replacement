import { useMemo, useState } from 'react';

import { useEscalateFailure, useFailures, useTransfers } from '@/api/hooks';
import type { Failure, Transfer } from '@/api/types';
import { Badge, ReasonBadge } from '@/components/Badge';
import { EmptyState, errorMessage, QueryState } from '@/components/Feedback';
import { ResolveFailureModal } from '@/components/ResolveFailureModal';
import { useToast } from '@/components/Toast';
import { downloadCsv, formatDateShort } from '@/lib/format';

const REASONS = [
  'NO_MAPPING',
  'VALIDATION_ERROR',
  'MISSING_PICKLIST',
  'CORRUPT_ARCHIVE',
  'VAULT_API_ERROR',
  'RATE_LIMIT',
];

export function UnclassifiedPage() {
  const [status, setStatus] = useState('OPEN');
  const [reason, setReason] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<{ failure: Failure; transfer?: Transfer } | null>(null);

  const failures = useFailures({ status, reason: reason || undefined });
  const transfers = useTransfers({ limit: 500 });
  const escalate = useEscalateFailure();
  const toast = useToast();

  const transferById = useMemo(() => {
    const map = new Map<string, Transfer>();
    for (const transfer of transfers.data ?? []) map.set(transfer.transfer_id, transfer);
    return map;
  }, [transfers.data]);

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (failures.data ?? [])
      .map((failure) => ({ failure, transfer: transferById.get(failure.transfer_id) }))
      .filter(({ failure, transfer }) => {
        if (!term) return true;
        return (
          transfer?.file_name.toLowerCase().includes(term) ||
          transfer?.source_path.toLowerCase().includes(term) ||
          failure.failure_detail?.toLowerCase().includes(term)
        );
      });
  }, [failures.data, transferById, search]);

  function toggle(failureId: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(failureId)) next.delete(failureId);
      else next.add(failureId);
      return next;
    });
  }

  async function notifyCro() {
    let done = 0;
    for (const failureId of selected) {
      try {
        await escalate.mutateAsync({ failureId });
        done += 1;
      } catch (error) {
        toast.push(errorMessage(error), 'danger');
        return;
      }
    }
    toast.push(`${done} item(s) escalated and assigned for CRO follow-up.`, 'ok');
    setSelected(new Set());
  }

  function exportCsv() {
    downloadCsv(
      `unclassified-${new Date().toISOString().slice(0, 10)}.csv`,
      rows.map(({ failure, transfer }) => ({
        failure_id: failure.failure_id,
        file_name: transfer?.file_name ?? '',
        source_path: transfer?.source_path ?? '',
        study: transfer?.resolved_metadata?.study ?? transfer?.source_path.split('/')[0] ?? '',
        reason: failure.failure_reason,
        detail: failure.failure_detail ?? '',
        resolution_status: failure.resolution_status,
        assigned_to: failure.assigned_to ?? '',
        created_at: failure.created_at ?? '',
      })),
    );
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Unclassified Documents</h1>
          <p>
            Files the pipeline refused to upload. Nothing is deleted from MBox: each item stays here until a human
            supplies the missing metadata or escalates it to the CRO.
          </p>
        </div>
        <button type="button" className="btn" onClick={exportCsv} disabled={rows.length === 0}>
          ⬇ Export CSV
        </button>
      </div>

      <section className="card">
        <div className="filter-bar">
          <div className="field">
            <label htmlFor="f-status">Status</label>
            <select id="f-status" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="OPEN">Open</option>
              <option value="ESCALATED">Escalated</option>
              <option value="RESOLVED">Resolved</option>
              <option value="ALL">All</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="f-reason">Reason</label>
            <select id="f-reason" value={reason} onChange={(event) => setReason(event.target.value)}>
              <option value="">All reasons</option>
              {REASONS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ flex: 1, minWidth: 220 }}>
            <label htmlFor="f-search">Search</label>
            <input
              id="f-search"
              type="text"
              placeholder="File name, path or failure detail"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
        </div>

        <div className="card-body tight">
          <QueryState isLoading={failures.isLoading} error={failures.error}>
            {rows.length === 0 ? (
              <EmptyState icon="🎉" title="Nothing unclassified" hint="Every processed file resolved cleanly." />
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th style={{ width: 36 }} />
                      <th>File name</th>
                      <th>Study</th>
                      <th>Reason</th>
                      <th>Detail</th>
                      <th>Date</th>
                      <th>Status</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(({ failure, transfer }) => (
                      <tr key={failure.failure_id}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Select failure ${failure.failure_id}`}
                            checked={selected.has(failure.failure_id)}
                            onChange={() => toggle(failure.failure_id)}
                          />
                        </td>
                        <td className="truncate" title={transfer?.source_path}>
                          {transfer?.file_name ?? `transfer ${failure.transfer_id.slice(0, 8)}`}
                        </td>
                        <td>{transfer?.source_path.split('/')[0] ?? '—'}</td>
                        <td>
                          <ReasonBadge reason={failure.failure_reason} />
                        </td>
                        <td className="truncate" title={failure.failure_detail ?? ''}>
                          {failure.failure_detail ?? '—'}
                        </td>
                        <td>{formatDateShort(failure.created_at)}</td>
                        <td>
                          <Badge
                            tone={
                              failure.resolution_status === 'RESOLVED'
                                ? 'ok'
                                : failure.resolution_status === 'ESCALATED'
                                  ? 'info'
                                  : 'warn'
                            }
                          >
                            {failure.resolution_status === 'ESCALATED' ? 'Notified' : failure.resolution_status}
                          </Badge>
                        </td>
                        <td>
                          {failure.resolution_status !== 'RESOLVED' ? (
                            <button
                              type="button"
                              className="btn btn-sm"
                              onClick={() => setEditing({ failure, transfer })}
                            >
                              ✏ Assign metadata
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </QueryState>
        </div>

        {selected.size > 0 ? (
          <div className="filter-bar" style={{ borderTop: '1px solid var(--line)', borderBottom: 'none' }}>
            <span>
              Selected: <strong>{selected.size}</strong> file(s)
            </span>
            <button type="button" className="btn" disabled={escalate.isPending} onClick={notifyCro}>
              📧 Notify CRO
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setSelected(new Set())}>
              Clear selection
            </button>
          </div>
        ) : null}
      </section>

      {editing ? (
        <ResolveFailureModal
          failure={editing.failure}
          transfer={editing.transfer}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </>
  );
}
