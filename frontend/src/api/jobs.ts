import type { Job, Transfer, TransferStatus } from './types';

const EMPTY_COUNTS: Record<TransferStatus, number> = {
  PENDING: 0,
  PROCESSING: 0,
  SUCCESS: 0,
  FAILED: 0,
  EXCEPTION: 0,
  DUPLICATE_SKIPPED: 0,
  SAM_PENDING: 0,
};

export function studyOf(transfer: Transfer): string {
  return transfer.resolved_metadata?.study ?? transfer.source_path.split('/')[0] ?? 'Unknown';
}

function jobStatus(counts: Record<TransferStatus, number>): Job['status'] {
  if (counts.PENDING + counts.PROCESSING > 0) return 'RUNNING';
  const bad = counts.EXCEPTION + counts.FAILED + counts.SAM_PENDING;
  const good = counts.SUCCESS + counts.DUPLICATE_SKIPPED;
  if (bad === 0) return 'DONE';
  return good === 0 ? 'FAILED' : 'PARTIAL';
}

/** Groups transfers into migration jobs by correlation id, newest first. */
export function groupIntoJobs(transfers: Transfer[]): Job[] {
  const byCorrelation = new Map<string, Transfer[]>();
  for (const transfer of transfers) {
    const bucket = byCorrelation.get(transfer.correlation_id);
    if (bucket) bucket.push(transfer);
    else byCorrelation.set(transfer.correlation_id, [transfer]);
  }

  const jobs: Job[] = [];
  for (const [correlationId, items] of byCorrelation) {
    const counts = { ...EMPTY_COUNTS };
    for (const item of items) counts[item.status] += 1;

    const studies = new Set(items.map(studyOf));
    const timestamps = items.map((i) => i.created_at).filter((t): t is string => Boolean(t));
    const updates = items.map((i) => i.updated_at).filter((t): t is string => Boolean(t));

    jobs.push({
      jobId: `J-${correlationId.slice(0, 8).toUpperCase()}`,
      correlationId,
      study: studies.size === 1 ? [...studies][0] : `${studies.size} studies`,
      status: jobStatus(counts),
      startedAt: timestamps.sort()[0] ?? null,
      updatedAt: updates.sort().at(-1) ?? null,
      initiatedBy: items.some((i) => i.initiated_by === 'USER') ? 'User' : 'Watcher',
      counts,
      total: items.length,
      transfers: items,
    });
  }

  return jobs.sort((a, b) => (b.startedAt ?? '').localeCompare(a.startedAt ?? ''));
}
