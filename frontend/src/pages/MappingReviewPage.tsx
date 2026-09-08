import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { useFailures, useJobs, useRetryTransfer } from '@/api/hooks';
import type { Failure, Transfer } from '@/api/types';
import { StatusBadge } from '@/components/Badge';
import { EmptyState, errorMessage, Notice, QueryState, Spinner } from '@/components/Feedback';
import { ResolveFailureModal } from '@/components/ResolveFailureModal';
import { useToast } from '@/components/Toast';
import { formatDateTime } from '@/lib/format';

const UNRESOLVED = new Set(['EXCEPTION', 'FAILED', 'SAM_PENDING']);

export function MappingReviewPage() {
  const { correlationId } = useParams();
  const navigate = useNavigate();
  const { jobs, isLoading, error } = useJobs();
  const failures = useFailures({ status: 'ALL' });
  const retry = useRetryTransfer();
  const toast = useToast();
  const [editing, setEditing] = useState<{ failure: Failure; transfer: Transfer } | null>(null);

  const job = useMemo(
    () => jobs.find((item) => item.correlationId === correlationId) ?? jobs[0],
    [jobs, correlationId],
  );

  const failureByTransfer = useMemo(() => {
    const map = new Map<string, Failure>();
    for (const failure of failures.data ?? []) {
      const existing = map.get(failure.transfer_id);
      if (!existing || failure.failure_id > existing.failure_id) map.set(failure.transfer_id, failure);
    }
    return map;
  }, [failures.data]);

  async function retryAllUnresolved() {
    if (!job) return;
    const targets = job.transfers.filter((t) => UNRESOLVED.has(t.status));
    let succeeded = 0;
    for (const transfer of targets) {
      try {
        const result = await retry.mutateAsync(transfer.transfer_id);
        if (result.status === 'SUCCESS') succeeded += 1;
      } catch (err) {
        toast.push(errorMessage(err), 'danger');
        return;
      }
    }
    toast.push(`Re-processed ${targets.length} file(s); ${succeeded} now archived.`, succeeded ? 'ok' : 'warn');
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Mapping Review{job ? ` — ${job.jobId} (${job.study})` : ''}</h1>
          <p>
            Metadata resolved by the mapping engine, in precedence order EXACT → REGEX → STUDY DEFAULT → GLOBAL
            DEFAULT. Files that could not be resolved were queued rather than uploaded.
          </p>
        </div>
        <div className="btn-row">
          <button type="button" className="btn" onClick={() => navigate('/dashboard')}>
            ← Back
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!job || retry.isPending || job.transfers.every((t) => !UNRESOLVED.has(t.status))}
            onClick={retryAllUnresolved}
          >
            {retry.isPending ? <Spinner /> : '✅'} Re-process unresolved
          </button>
        </div>
      </div>

      <QueryState isLoading={isLoading} error={error}>
        {!job ? (
          <EmptyState icon="🗃" title="No migration jobs found" hint="Start one from the Upload page." />
        ) : (
          <>
            <div className="btn-row" style={{ marginBottom: 14 }}>
              <span className="badge" data-tone="ok">
                ✔ Mapped: {job.counts.SUCCESS} files
              </span>
              <span className="badge" data-tone="warn">
                ⚠ Unclassified: {job.counts.EXCEPTION + job.counts.FAILED + job.counts.SAM_PENDING} files
              </span>
              <span className="badge">⧉ Duplicates: {job.counts.DUPLICATE_SKIPPED}</span>
              <span className="muted" style={{ marginLeft: 'auto' }}>
                Correlation id <span className="mono">{job.correlationId}</span>
              </span>
            </div>

            {jobs.length > 1 ? (
              <div className="field" style={{ maxWidth: 460 }}>
                <label htmlFor="job-select">Job</label>
                <select
                  id="job-select"
                  value={job.correlationId}
                  onChange={(event) => navigate(`/mapping/${event.target.value}`)}
                >
                  {jobs.map((item) => (
                    <option key={item.correlationId} value={item.correlationId}>
                      {item.jobId} · {item.study} · {item.total} file(s) · {formatDateTime(item.startedAt)}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}

            <section className="card">
              <div className="card-head">
                <h2>Resolved metadata</h2>
                <span className="muted">{job.total} file(s)</span>
              </div>
              <div className="card-body tight">
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>File name</th>
                        <th>Study</th>
                        <th>Type</th>
                        <th>Subtype</th>
                        <th>Classification</th>
                        <th>Status</th>
                        <th>Vault ID</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {job.transfers.map((transfer) => {
                        const meta = transfer.resolved_metadata ?? {};
                        const failure = failureByTransfer.get(transfer.transfer_id);
                        const unresolved = UNRESOLVED.has(transfer.status);
                        return (
                          <tr key={transfer.transfer_id}>
                            <td className="truncate" title={transfer.source_path}>
                              {transfer.file_name}
                            </td>
                            <td>{meta.study ?? '—'}</td>
                            <td>{meta.document_type ?? <span className="muted">--</span>}</td>
                            <td>{meta.document_subtype ?? <span className="muted">--</span>}</td>
                            <td>{meta.classification ?? <span className="muted">UNMAP</span>}</td>
                            <td>
                              <StatusBadge status={transfer.status} />
                            </td>
                            <td className="mono">{transfer.vault_document_id ?? '—'}</td>
                            <td>
                              {unresolved && failure ? (
                                <button
                                  type="button"
                                  className="btn btn-sm"
                                  onClick={() => setEditing({ failure, transfer })}
                                >
                                  ✏ Edit mapping
                                </button>
                              ) : null}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>

            {job.counts.SAM_PENDING > 0 ? (
              <Notice tone="warn">
                {job.counts.SAM_PENDING} file(s) carry a value that VTMF does not have configured. They are held in
                the SAM action queue and were deliberately not uploaded. Request the value, refresh the reference
                data from Settings, then re-process.
              </Notice>
            ) : null}
            {job.counts.EXCEPTION + job.counts.FAILED > 0 ? (
              <Notice tone="warn">
                {job.counts.EXCEPTION + job.counts.FAILED} file(s) could not be mapped or validated. They are listed
                under Unclassified Documents and remain in MBox until resolved.
              </Notice>
            ) : null}
          </>
        )}
      </QueryState>

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
