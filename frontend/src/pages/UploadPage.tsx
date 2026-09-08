import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useBrowse, useSubmitTransfers } from '@/api/hooks';
import { EmptyState, errorMessage, Notice, QueryState, Spinner } from '@/components/Feedback';
import { Modal } from '@/components/Modal';
import { useToast } from '@/components/Toast';
import { formatBytes, formatDateTime } from '@/lib/format';

interface ParsedPath {
  study: string;
  country: string;
  site: string;
  category: string;
}

function parseRelativePath(path: string): ParsedPath {
  const parts = path.split('/');
  return {
    study: parts[0] ?? '—',
    country: parts[1] ?? '—',
    site: parts[2] ?? '—',
    category: parts[3] ?? '—',
  };
}

const MIN_FOLDER_DEPTH = 3;

export function UploadPage() {
  const [path, setPath] = useState('');
  const [selected, setSelected] = useState<Record<string, number | null>>({});
  const [overrideDuplicates, setOverrideDuplicates] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

  const browse = useBrowse(path);
  const submit = useSubmitTransfers();
  const toast = useToast();
  const navigate = useNavigate();

  const segments = path ? path.split('/') : [];
  const selectedPaths = useMemo(() => Object.keys(selected), [selected]);

  const folderFiles = (browse.data ?? []).filter((entry) => !entry.is_dir);
  const allFolderFilesSelected =
    folderFiles.length > 0 && folderFiles.every((entry) => entry.relative_path in selected);

  function toggle(relativePath: string, size: number | null) {
    setSelected((current) => {
      const next = { ...current };
      if (relativePath in next) delete next[relativePath];
      else next[relativePath] = size;
      return next;
    });
  }

  function toggleAllInFolder() {
    setSelected((current) => {
      const next = { ...current };
      for (const entry of folderFiles) {
        if (allFolderFilesSelected) delete next[entry.relative_path];
        else next[entry.relative_path] = entry.size_bytes;
      }
      return next;
    });
  }

  async function startMigration() {
    try {
      const response = await submit.mutateAsync({
        files: selectedPaths.map((source_path) => ({
          source_path,
          initiated_by: 'USER',
          override_duplicate: overrideDuplicates,
        })),
      });
      const succeeded = response.results.filter((r) => r.status === 'SUCCESS').length;
      toast.push(
        `Job started: ${succeeded}/${response.submitted} files archived. Review the remainder below.`,
        succeeded === response.submitted ? 'ok' : 'warn',
      );
      setSelected({});
      setShowPreview(false);
      navigate(`/mapping/${response.correlation_id}`);
    } catch (error) {
      toast.push(errorMessage(error), 'danger');
    }
  }

  const shallowSelection = selectedPaths.filter((p) => p.split('/').length - 1 < MIN_FOLDER_DEPTH);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Trigger New Migration</h1>
          <p>
            Select the study folders to archive after database lock. Ingestion is a batch operation: the watcher
            also picks these folders up automatically, and submitting here simply runs the same pipeline on demand.
          </p>
        </div>
      </div>

      <div className="grid-2">
        <section className="card">
          <div className="card-head">
            <h2>Source</h2>
          </div>
          <div className="card-body">
            <div className="field">
              <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink-700)' }}>Source system</span>
              <div className="radio-row" style={{ marginTop: 4 }}>
                <label>
                  <input type="radio" name="source" defaultChecked readOnly />
                  Inbox (MBox share)
                </label>
                <label title="Documents must land on the validated MBox share before archival.">
                  <input type="radio" name="source" disabled />
                  <span className="muted">Manual upload (disabled)</span>
                </label>
              </div>
            </div>

            <Notice tone="info">
              Files are read directly from the read-only MBox share, so nothing is uploaded through the browser.
              This keeps the source record untouched and preserves the original checksum for the audit trail.
            </Notice>

            <label className="checkbox">
              <input
                type="checkbox"
                checked={overrideDuplicates}
                onChange={(event) => setOverrideDuplicates(event.target.checked)}
              />
              <span>
                Re-process duplicates
                <br />
                <span className="help">
                  By default a file with the same path and checksum as a previous success is skipped. Vault uploads
                  stay idempotent either way.
                </span>
              </span>
            </label>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>Selection summary</h2>
            {selectedPaths.length > 0 ? (
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setSelected({})}>
                Clear
              </button>
            ) : null}
          </div>
          <div className="card-body">
            <dl className="kv">
              <dt>Files selected</dt>
              <dd>{selectedPaths.length}</dd>
              <dt>Total size</dt>
              <dd>{formatBytes(Object.values(selected).reduce<number>((sum, size) => sum + (size ?? 0), 0))}</dd>
              <dt>Studies</dt>
              <dd>{new Set(selectedPaths.map((p) => parseRelativePath(p).study)).size || '—'}</dd>
            </dl>

            {shallowSelection.length > 0 ? (
              <Notice tone="warn">
                {shallowSelection.length} file(s) sit above the required Study/Country/Site depth and will be
                rejected by the pipeline.
              </Notice>
            ) : null}

            <div className="btn-row" style={{ marginTop: 16 }}>
              <button
                type="button"
                className="btn"
                disabled={selectedPaths.length === 0}
                onClick={() => setShowPreview(true)}
              >
                Preview Files
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={selectedPaths.length === 0 || submit.isPending}
                onClick={startMigration}
              >
                {submit.isPending ? <Spinner /> : '▶'} Start Migration
              </button>
            </div>
          </div>
        </section>
      </div>

      <section className="card" style={{ marginTop: 16 }}>
        <div className="card-head">
          <h2>Browse Inbox</h2>
          {folderFiles.length > 0 ? (
            <button type="button" className="btn btn-sm" onClick={toggleAllInFolder}>
              {allFolderFilesSelected ? 'Deselect folder' : 'Select all files in folder'}
            </button>
          ) : null}
        </div>
        <div className="card-body">
          <nav className="breadcrumbs" aria-label="Folder path">
            <button type="button" onClick={() => setPath('')}>
              MBox root
            </button>
            {segments.map((segment, index) => (
              <span key={`${segment}-${index}`}>
                <span className="sep"> / </span>
                <button type="button" onClick={() => setPath(segments.slice(0, index + 1).join('/'))}>
                  {segment}
                </button>
              </span>
            ))}
          </nav>

          <QueryState isLoading={browse.isLoading} error={browse.error}>
            {(browse.data ?? []).length === 0 ? (
              <EmptyState icon="📁" title="This folder is empty" />
            ) : (
              <div className="browser">
                {browse.data?.map((entry) => (
                  <div className="browser-row" key={entry.relative_path}>
                    {entry.is_dir ? (
                      <span style={{ width: 16 }} />
                    ) : (
                      <input
                        type="checkbox"
                        aria-label={`Select ${entry.name}`}
                        checked={entry.relative_path in selected}
                        onChange={() => toggle(entry.relative_path, entry.size_bytes)}
                      />
                    )}
                    <div className="name">
                      <span aria-hidden>{entry.is_dir ? '📁' : '📄'}</span>
                      {entry.is_dir ? (
                        <button type="button" onClick={() => setPath(entry.relative_path)}>
                          {entry.name}
                        </button>
                      ) : (
                        <span className="truncate">{entry.name}</span>
                      )}
                    </div>
                    <span className="meta">{entry.is_dir ? 'Folder' : formatBytes(entry.size_bytes)}</span>
                    <span className="meta">{formatDateTime(entry.modified_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </QueryState>

          {selectedPaths.length > 0 ? (
            <div className="selection-bar">
              <span>
                <strong>{selectedPaths.length}</strong> file(s) selected across the whole tree
              </span>
              <button type="button" className="btn btn-sm" onClick={() => setShowPreview(true)}>
                Preview
              </button>
            </div>
          ) : null}
        </div>
      </section>

      {showPreview ? (
        <Modal
          title={`Preview — ${selectedPaths.length} file(s)`}
          onClose={() => setShowPreview(false)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setShowPreview(false)}>
                Back
              </button>
              <button type="button" className="btn btn-primary" disabled={submit.isPending} onClick={startMigration}>
                {submit.isPending ? <Spinner /> : '▶'} Start Migration
              </button>
            </>
          }
        >
          <p className="muted">
            Metadata below is derived from the folder hierarchy. The mapping engine resolves the final Vault
            document type, subtype and classification during processing.
          </p>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Study</th>
                  <th>Country</th>
                  <th>Site</th>
                  <th>Category</th>
                </tr>
              </thead>
              <tbody>
                {selectedPaths.map((p) => {
                  const parsed = parseRelativePath(p);
                  return (
                    <tr key={p}>
                      <td className="truncate" title={p}>
                        {p.split('/').pop()}
                      </td>
                      <td>{parsed.study}</td>
                      <td>{parsed.country}</td>
                      <td>{parsed.site}</td>
                      <td>{parsed.category}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
