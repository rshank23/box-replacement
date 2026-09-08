import { useState } from 'react';

import { useAudit, usePocStatus, useResetPoc, useRunPoc } from '@/api/hooks';
import type { PocRunResponse, Transfer } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { StatusBadge } from '@/components/Badge';
import { EmptyState, errorMessage, Notice, QueryState, Spinner } from '@/components/Feedback';
import { FolderPanel } from '@/components/FolderPanel';
import { useToast } from '@/components/Toast';
import { formatDateTime } from '@/lib/format';

const OUTCOME_HINTS: Record<string, string> = {
  SUCCESS: 'Filed to destination',
  SAM_PENDING: 'Held — value not configured in VTMF',
  EXCEPTION: 'Unclassified / failure queue',
  DUPLICATE_SKIPPED: 'Skipped — already archived',
};

function destinationOf(transfer: Transfer): string {
  const meta = transfer.resolved_metadata ?? {};
  if (transfer.status === 'SUCCESS') {
    return [meta.study, meta.country, meta.site, meta.document_type, meta.document_subtype]
      .filter(Boolean)
      .join(' / ');
  }
  if (transfer.status === 'EXCEPTION' && transfer.vault_document_id) {
    return `unclassified / ${[meta.study, meta.country, meta.site].filter(Boolean).join(' / ')}`;
  }
  return '—';
}

export function PocPage() {
  const status = usePocStatus();
  const run = useRunPoc();
  const reset = useResetPoc();
  const toast = useToast();
  const { isAdmin, openAccess } = useAuth();
  const [run_, setRun] = useState<PocRunResponse | null>(null);

  const correlationId = run_?.correlation_id ?? null;
  const audit = useAudit({ correlation_id: correlationId ?? undefined, limit: 300 });
  const runTransfers = run_?.results ?? [];

  const counts = status.data?.counts;
  const isFilesystem = status.data?.vault_client === 'filesystem';

  async function onRun() {
    try {
      const response = await run.mutateAsync(true);
      setRun(response);
      const archived = response.results.filter((r) => r.status === 'SUCCESS').length;
      toast.push(
        `Migrated ${archived}/${response.submitted} files. ${response.skipped_extension} skipped on extension.`,
        archived === response.submitted ? 'ok' : 'warn',
      );
    } catch (error) {
      toast.push(errorMessage(error), 'danger');
    }
  }

  async function onReset() {
    try {
      await reset.mutateAsync();
      setRun(null);
      toast.push('Destination and unclassified folders emptied. The audit trail is unchanged.', 'ok');
    } catch (error) {
      toast.push(errorMessage(error), 'danger');
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Migration proof of concept</h1>
          <p>
            Runs the full production pipeline — nested ZIP extraction, checksum de-duplication, mapping,
            validation, rate limiting and audit — against local folders instead of Veeva Vault.
          </p>
        </div>
        <div className="btn-row">
          <button
            type="button"
            className="btn"
            disabled={reset.isPending || (!isAdmin && !openAccess) || !isFilesystem}
            title={isFilesystem ? undefined : 'Requires VAULT_CLIENT=filesystem'}
            onClick={onReset}
          >
            {reset.isPending ? <Spinner /> : '↺'} Reset folders
          </button>
          <button type="button" className="btn btn-primary" disabled={run.isPending} onClick={onRun}>
            {run.isPending ? <Spinner /> : '▶'} Run migration
          </button>
        </div>
      </div>

      <QueryState isLoading={status.isLoading} error={status.error}>
        {!isFilesystem ? (
          <Notice tone="warn">
            The active Vault client is <span className="mono">{status.data?.vault_client}</span>. Files will not be
            written to the destination folder. Start the API with <span className="mono">VAULT_CLIENT=filesystem</span>{' '}
            to see documents land on disk.
          </Notice>
        ) : null}

        <section className="card" style={{ marginBottom: 16 }}>
          <div className="card-body">
            <dl className="kv poc-config">
              <dt>Source</dt>
              <dd className="mono">{status.data?.source_root}</dd>
              <dt>Destination</dt>
              <dd className="mono">{status.data?.destination_root}</dd>
              <dt>Unclassified</dt>
              <dd className="mono">{status.data?.unclassified_root}</dd>
              <dt>Rate limit</dt>
              <dd>{status.data?.rate_limit_per_sec}/second to the destination</dd>
              <dt>Nested archives</dt>
              <dd>
                {status.data?.zip_max_depth === -1
                  ? 'Extracted at every level'
                  : `Extracted to depth ${status.data?.zip_max_depth}`}
              </dd>
              <dt>Unmapped policy</dt>
              <dd>
                {status.data?.unmapped_policy === 'unclassified'
                  ? 'File to Unclassified and raise in the failure queue'
                  : 'Hold in the failure queue only'}
              </dd>
            </dl>
          </div>
        </section>

        <div className="poc-grid">
          <FolderPanel
            root="source"
            title="Source"
            subtitle="Files dropped by the CRO"
            tone="source"
            count={counts?.source}
          />
          <FolderPanel
            root="destination"
            title="Destination"
            subtitle="Study / Country / Site / Type / Subtype"
            tone="ok"
            count={counts?.destination}
          />
          <FolderPanel
            root="unclassified"
            title="Unclassified"
            subtitle="No mapping rule matched"
            tone="warn"
            count={counts?.unclassified}
          />
        </div>
      </QueryState>

      {correlationId ? (
        <>
          <section className="card" style={{ marginTop: 16 }}>
            <div className="card-head">
              <h2>Per-file outcome</h2>
              <span className="muted mono">{correlationId}</span>
            </div>
            <div className="card-body tight">
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Source path</th>
                      <th>Status</th>
                      <th>Filed to</th>
                      <th>Document</th>
                      <th>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runTransfers.map((transfer) => (
                      <tr key={transfer.transfer_id}>
                        <td className="truncate mono" title={transfer.source_path}>
                          {transfer.source_path}
                        </td>
                        <td>
                          <StatusBadge status={transfer.status} />
                          <div className="muted" style={{ fontSize: 11 }}>
                            {OUTCOME_HINTS[transfer.status] ?? ''}
                          </div>
                        </td>
                        <td className="truncate" title={destinationOf(transfer)}>
                          {destinationOf(transfer)}
                        </td>
                        <td className="mono">{transfer.vault_document_id ?? '—'}</td>
                        <td className="truncate" title={transfer.message ?? ''}>
                          {transfer.message ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section className="card" style={{ marginTop: 16 }}>
            <div className="card-head">
              <h2>Pipeline log</h2>
              <span className="muted">Append-only audit trail for this run</span>
            </div>
            <div className="card-body tight">
              <QueryState isLoading={audit.isLoading} error={audit.error}>
                {(audit.data ?? []).length === 0 ? (
                  <EmptyState icon="🧾" title="No audit entries for this run yet" />
                ) : (
                  <div className="log-view">
                    {[...(audit.data ?? [])].reverse().map((entry) => (
                      <div className="log-line" key={entry.audit_id}>
                        <span className="log-time">{formatDateTime(entry.timestamp)}</span>
                        <span className="log-action" data-action={entry.action}>
                          {entry.action}
                        </span>
                        <span className="log-detail">{JSON.stringify(entry.details ?? {})}</span>
                      </div>
                    ))}
                  </div>
                )}
              </QueryState>
            </div>
          </section>
        </>
      ) : (
        <Notice tone="info">
          Press <strong>Run migration</strong> to process every file in the source folder. Each run gets its own
          correlation id, and the per-file outcome and audit log appear here.
        </Notice>
      )}
    </>
  );
}
