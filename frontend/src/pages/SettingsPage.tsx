import { useState } from 'react';

import {
  useCreateMapping,
  useDeactivateMapping,
  useMappings,
  useRefreshPicklists,
  useUpdateMapping,
} from '@/api/hooks';
import type { MappingRule, MappingRuleInput, MatchType } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { Badge } from '@/components/Badge';
import { EmptyState, errorMessage, Notice, QueryState, Spinner } from '@/components/Feedback';
import { Modal } from '@/components/Modal';
import { useToast } from '@/components/Toast';
import { formatDateTime } from '@/lib/format';

const MATCH_TYPES: MatchType[] = ['EXACT', 'REGEX', 'STUDY_DEFAULT', 'GLOBAL_DEFAULT'];

const EMPTY_RULE: MappingRuleInput = {
  source_pattern: '',
  target_study: null,
  target_country: null,
  target_site: null,
  document_type: null,
  document_subtype: null,
  classification: null,
  default_metadata: null,
  match_type: 'EXACT',
  priority: 100,
  is_active: true,
};

function RuleEditor({
  rule,
  onClose,
}: {
  rule: MappingRule | null;
  onClose: () => void;
}) {
  const create = useCreateMapping();
  const update = useUpdateMapping();
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<MappingRuleInput>(
    rule
      ? {
          source_pattern: rule.source_pattern,
          target_study: rule.target_study,
          target_country: rule.target_country,
          target_site: rule.target_site,
          document_type: rule.document_type,
          document_subtype: rule.document_subtype,
          classification: rule.classification,
          default_metadata: rule.default_metadata,
          match_type: rule.match_type,
          priority: rule.priority,
          is_active: rule.is_active,
        }
      : EMPTY_RULE,
  );

  function set<K extends keyof MappingRuleInput>(key: K, value: MappingRuleInput[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function save() {
    setError(null);
    try {
      if (rule) {
        await update.mutateAsync({ mappingId: rule.mapping_id, changes: draft });
        toast.push(`Rule #${rule.mapping_id} updated; a new version was recorded.`, 'ok');
      } else {
        await create.mutateAsync(draft);
        toast.push('Mapping rule created.', 'ok');
      }
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const busy = create.isPending || update.isPending;
  const textFields: Array<[keyof MappingRuleInput, string, string]> = [
    ['target_study', 'Target study', 'STUDY-001'],
    ['target_country', 'Target country', 'US'],
    ['target_site', 'Target site', 'SITE-101'],
    ['document_type', 'Document type', 'Trial Management'],
    ['document_subtype', 'Document subtype', 'Monitoring Plan'],
    ['classification', 'Classification', 'Essential Document'],
  ];

  return (
    <Modal
      title={rule ? `Edit mapping rule #${rule.mapping_id}` : 'New mapping rule'}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || draft.source_pattern.trim().length === 0}
            onClick={save}
          >
            {busy ? <Spinner /> : null} Save
          </button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="rule-pattern">Source pattern</label>
        <input
          id="rule-pattern"
          type="text"
          value={draft.source_pattern}
          placeholder="STUDY-001/US/SITE-101/Trial Management"
          onChange={(event) => set('source_pattern', event.target.value)}
        />
        <span className="help">
          EXACT matches the full relative path or its folder, REGEX is matched against the relative path,
          STUDY_DEFAULT matches the study folder name, GLOBAL_DEFAULT matches everything.
        </span>
      </div>

      <div className="form-grid">
        <div className="field">
          <label htmlFor="rule-match">Match type</label>
          <select
            id="rule-match"
            value={draft.match_type}
            onChange={(event) => set('match_type', event.target.value as MatchType)}
          >
            {MATCH_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="rule-priority">Priority</label>
          <input
            id="rule-priority"
            type="number"
            value={draft.priority}
            min={1}
            onChange={(event) => set('priority', Number(event.target.value))}
          />
          <span className="help">Lower wins inside the same match type.</span>
        </div>
        {textFields.map(([key, label, placeholder]) => (
          <div className="field" key={key as string}>
            <label htmlFor={`rule-${key as string}`}>{label}</label>
            <input
              id={`rule-${key as string}`}
              type="text"
              placeholder={placeholder}
              value={(draft[key] as string | null) ?? ''}
              onChange={(event) => set(key, (event.target.value || null) as never)}
            />
          </div>
        ))}
      </div>

      <label className="checkbox">
        <input type="checkbox" checked={draft.is_active} onChange={(event) => set('is_active', event.target.checked)} />
        <span>Rule is active</span>
      </label>

      {error ? <Notice tone="danger">{error}</Notice> : null}
    </Modal>
  );
}

export function SettingsPage() {
  const { subject, roles, isAdmin, openAccess, token } = useAuth();
  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<{ rule: MappingRule | null } | null>(null);

  const mappings = useMappings(!showInactive);
  const deactivate = useDeactivateMapping();
  const refresh = useRefreshPicklists();
  const toast = useToast();

  async function onRefreshPicklists() {
    try {
      const response = await refresh.mutateAsync();
      const total = Object.values(response.counts).reduce((sum, n) => sum + n, 0);
      toast.push(`Reference data refreshed: ${total} values across ${Object.keys(response.counts).length} sets.`, 'ok');
    } catch (error) {
      toast.push(errorMessage(error), 'danger');
    }
  }

  async function onDeactivate(rule: MappingRule) {
    try {
      await deactivate.mutateAsync(rule.mapping_id);
      toast.push(`Rule #${rule.mapping_id} deactivated. Historical mappings remain intact.`, 'ok');
    } catch (error) {
      toast.push(errorMessage(error), 'danger');
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>
            Mapping configuration and reference data. Every change here is written to the audit trail with before
            and after values.
          </p>
        </div>
      </div>

      <div className="stack">
        <section className="card">
          <div className="card-head">
            <h2>Session</h2>
          </div>
          <div className="card-body">
            <dl className="kv">
              <dt>Signed in as</dt>
              <dd>{subject || '—'}</dd>
              <dt>Roles</dt>
              <dd>{roles.length ? roles.join(', ') : '—'}</dd>
              <dt>Administrator</dt>
              <dd>{isAdmin ? 'Yes' : 'No'}</dd>
              <dt>Bearer token</dt>
              <dd>{token ? 'Issued (held in session storage only)' : 'None'}</dd>
            </dl>
            {openAccess ? (
              <Notice tone="warn">
                The API is running with <span className="mono">AUTH_ENABLED=false</span>. That is acceptable for
                local development only — validated environments must enforce SSO and role checks.
              </Notice>
            ) : null}
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>VTMF reference data</h2>
            <button type="button" className="btn btn-sm" disabled={refresh.isPending} onClick={onRefreshPicklists}>
              {refresh.isPending ? <Spinner /> : '⟳'} Refresh cache
            </button>
          </div>
          <div className="card-body">
            <p className="muted" style={{ margin: 0 }}>
              Validation compares resolved metadata against a local copy of the study, country and site picklists
              plus the Type → Subtype → Classification hierarchy. Refresh after a SAM request is approved so held
              documents can be re-processed.
            </p>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>Mapping rules</h2>
            <div className="btn-row">
              <label className="checkbox" style={{ margin: 0 }}>
                <input
                  type="checkbox"
                  checked={showInactive}
                  onChange={(event) => setShowInactive(event.target.checked)}
                />
                <span>Show inactive</span>
              </label>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={!isAdmin && !openAccess}
                title={isAdmin || openAccess ? undefined : 'Administrator role required'}
                onClick={() => setEditing({ rule: null })}
              >
                + New rule
              </button>
            </div>
          </div>
          <div className="card-body tight">
            <QueryState isLoading={mappings.isLoading} error={mappings.error}>
              {(mappings.data ?? []).length === 0 ? (
                <EmptyState icon="🧭" title="No mapping rules yet" hint="Seed them or create the first rule." />
              ) : (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>ID</th>
                        <th>Match</th>
                        <th>Source pattern</th>
                        <th>Type / Subtype</th>
                        <th>Classification</th>
                        <th>Prio</th>
                        <th>Ver</th>
                        <th>Updated</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {mappings.data?.map((rule) => (
                        <tr key={rule.mapping_id}>
                          <td className="mono">#{rule.mapping_id}</td>
                          <td>
                            <Badge tone={rule.match_type === 'EXACT' ? 'info' : 'neutral'}>{rule.match_type}</Badge>
                          </td>
                          <td className="mono truncate" title={rule.source_pattern}>
                            {rule.source_pattern}
                          </td>
                          <td>
                            {rule.document_type ?? '—'}
                            <br />
                            <span className="muted">{rule.document_subtype ?? '—'}</span>
                          </td>
                          <td>{rule.classification ?? '—'}</td>
                          <td>{rule.priority}</td>
                          <td>v{rule.version}</td>
                          <td>{formatDateTime(rule.updated_at)}</td>
                          <td>
                            <div className="btn-row">
                              <button
                                type="button"
                                className="btn btn-sm"
                                disabled={!isAdmin && !openAccess}
                                onClick={() => setEditing({ rule })}
                              >
                                Edit
                              </button>
                              {rule.is_active ? (
                                <button
                                  type="button"
                                  className="btn btn-sm btn-ghost"
                                  disabled={(!isAdmin && !openAccess) || deactivate.isPending}
                                  onClick={() => onDeactivate(rule)}
                                >
                                  Deactivate
                                </button>
                              ) : (
                                <Badge>Inactive</Badge>
                              )}
                            </div>
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
      </div>

      {editing ? <RuleEditor rule={editing.rule} onClose={() => setEditing(null)} /> : null}
    </>
  );
}
