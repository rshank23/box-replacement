import type { ReactNode } from 'react';

export type Tone = 'neutral' | 'ok' | 'warn' | 'danger' | 'info';

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className="badge" data-tone={tone}>
      {children}
    </span>
  );
}

const TRANSFER_TONES: Record<string, { tone: Tone; icon: string; label: string }> = {
  SUCCESS: { tone: 'ok', icon: '✔', label: 'Success' },
  PENDING: { tone: 'info', icon: '◷', label: 'Pending' },
  PROCESSING: { tone: 'info', icon: '⟳', label: 'Processing' },
  FAILED: { tone: 'danger', icon: '✕', label: 'Failed' },
  EXCEPTION: { tone: 'danger', icon: '!', label: 'Exception' },
  SAM_PENDING: { tone: 'warn', icon: '⚑', label: 'SAM pending' },
  DUPLICATE_SKIPPED: { tone: 'neutral', icon: '⧉', label: 'Duplicate' },
};

export function StatusBadge({ status }: { status: string }) {
  const config = TRANSFER_TONES[status] ?? { tone: 'neutral' as Tone, icon: '•', label: status };
  return (
    <Badge tone={config.tone}>
      <span aria-hidden>{config.icon}</span>
      {config.label}
    </Badge>
  );
}

const JOB_TONES: Record<string, { tone: Tone; icon: string; label: string }> = {
  DONE: { tone: 'ok', icon: '✔', label: 'Done' },
  PARTIAL: { tone: 'warn', icon: '⚠', label: 'Partial' },
  RUNNING: { tone: 'info', icon: '⟳', label: 'Running' },
  FAILED: { tone: 'danger', icon: '✕', label: 'Failed' },
};

export function JobStatusBadge({ status }: { status: string }) {
  const config = JOB_TONES[status] ?? { tone: 'neutral' as Tone, icon: '•', label: status };
  return (
    <Badge tone={config.tone}>
      <span aria-hidden>{config.icon}</span>
      {config.label}
    </Badge>
  );
}

const REASON_LABELS: Record<string, string> = {
  NO_MAPPING: 'No mapping',
  VALIDATION_ERROR: 'Validation error',
  VAULT_API_ERROR: 'Vault API error',
  RATE_LIMIT: 'Rate limited',
  CORRUPT_ARCHIVE: 'Corrupt archive',
  MISSING_PICKLIST: 'Missing picklist',
};

export function ReasonBadge({ reason }: { reason: string }) {
  const tone: Tone = reason === 'MISSING_PICKLIST' ? 'warn' : 'danger';
  return <Badge tone={tone}>{REASON_LABELS[reason] ?? reason}</Badge>;
}
