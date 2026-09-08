import { useState } from 'react';

import { useResolveFailure } from '@/api/hooks';
import type { Failure, MappingOverride, Transfer } from '@/api/types';
import { errorMessage, Notice, Spinner } from '@/components/Feedback';
import { Modal } from '@/components/Modal';
import { useToast } from '@/components/Toast';

const FIELDS: Array<{ key: keyof MappingOverride; label: string; placeholder: string }> = [
  { key: 'target_study', label: 'Study', placeholder: 'STUDY-001' },
  { key: 'target_country', label: 'Country', placeholder: 'US' },
  { key: 'target_site', label: 'Site', placeholder: 'SITE-101' },
  { key: 'document_type', label: 'Document type', placeholder: 'Trial Management' },
  { key: 'document_subtype', label: 'Document subtype', placeholder: 'Monitoring Plan' },
  { key: 'classification', label: 'Classification', placeholder: 'Essential Document' },
];

interface Props {
  failure: Failure;
  transfer?: Transfer;
  onClose: () => void;
}

export function ResolveFailureModal({ failure, transfer, onClose }: Props) {
  const resolve = useResolveFailure();
  const toast = useToast();

  const suggested = failure.suggested_mapping ?? {};
  const metadata = transfer?.resolved_metadata ?? {};

  const [values, setValues] = useState<Record<string, string>>({
    target_study: String(suggested.target_study ?? metadata.study ?? ''),
    target_country: String(suggested.target_country ?? metadata.country ?? ''),
    target_site: String(suggested.target_site ?? metadata.site ?? ''),
    document_type: String(suggested.document_type ?? metadata.document_type ?? ''),
    document_subtype: String(metadata.document_subtype ?? ''),
    classification: String(metadata.classification ?? ''),
  });
  const [persistAsRule, setPersistAsRule] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  const complete = FIELDS.every((field) => values[field.key as string]?.trim());

  async function onSubmit() {
    setError(null);
    const override: MappingOverride = {};
    for (const field of FIELDS) {
      const value = values[field.key as string]?.trim();
      if (value) override[field.key] = value as never;
    }
    try {
      const response = await resolve.mutateAsync({
        failureId: failure.failure_id,
        override,
        persistAsRule,
        reason: reason.trim() || undefined,
      });
      toast.push(
        `Transfer re-processed: ${response.transfer.status}`,
        response.transfer.status === 'SUCCESS' ? 'ok' : 'warn',
      );
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Modal
      title="Assign metadata manually"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" disabled={!complete || resolve.isPending} onClick={onSubmit}>
            {resolve.isPending ? <Spinner /> : null} Confirm &amp; Transfer
          </button>
        </>
      }
    >
      <dl className="kv" style={{ marginBottom: 18 }}>
        <dt>File</dt>
        <dd className="mono">{transfer?.file_name ?? '—'}</dd>
        <dt>Source path</dt>
        <dd className="mono">{transfer?.source_path ?? '—'}</dd>
        <dt>Failure reason</dt>
        <dd>{failure.failure_reason}</dd>
        <dt>Detail</dt>
        <dd>{failure.failure_detail ?? '—'}</dd>
      </dl>

      <div className="form-grid">
        {FIELDS.map((field) => (
          <div className="field" key={field.key as string}>
            <label htmlFor={`ov-${field.key as string}`}>{field.label}</label>
            <input
              id={`ov-${field.key as string}`}
              type="text"
              placeholder={field.placeholder}
              value={values[field.key as string] ?? ''}
              onChange={(event) =>
                setValues((current) => ({ ...current, [field.key as string]: event.target.value }))
              }
            />
          </div>
        ))}
      </div>

      <div className="field">
        <label htmlFor="resolve-reason">Reason for change</label>
        <textarea
          id="resolve-reason"
          rows={2}
          placeholder="Recorded in the audit trail alongside the override"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </div>

      <label className="checkbox">
        <input
          type="checkbox"
          checked={persistAsRule}
          onChange={(event) => setPersistAsRule(event.target.checked)}
        />
        <span>
          Save as a reusable mapping rule
          <br />
          <span className="help">Future files under the same source pattern map automatically.</span>
        </span>
      </label>

      {!complete ? (
        <Notice tone="warn">
          All six fields are required by VTMF. Values that are not yet valid picklist entries will be routed to the
          SAM action queue instead of being uploaded.
        </Notice>
      ) : null}
      {error ? <Notice tone="danger">{error}</Notice> : null}
    </Modal>
  );
}
