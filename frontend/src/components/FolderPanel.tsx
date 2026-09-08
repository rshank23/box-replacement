import { useState } from 'react';

import { useBrowse } from '@/api/hooks';
import type { FileRoot } from '@/api/types';
import { EmptyState, QueryState } from '@/components/Feedback';
import { formatBytes, formatDateTime } from '@/lib/format';

interface Props {
  root: FileRoot;
  title: string;
  subtitle: string;
  tone: 'source' | 'ok' | 'warn';
  count?: number;
}

export function FolderPanel({ root, title, subtitle, tone, count }: Props) {
  const [path, setPath] = useState('');
  const browse = useBrowse(path, true, root);
  const segments = path ? path.split('/') : [];

  return (
    <section className="card folder-panel" data-tone={tone}>
      <div className="card-head">
        <div>
          <h2>{title}</h2>
          <div className="muted" style={{ fontSize: 12 }}>
            {subtitle}
          </div>
        </div>
        <span className="badge" data-tone={tone === 'source' ? 'info' : tone}>
          {count ?? 0} file{count === 1 ? '' : 's'}
        </span>
      </div>

      <div className="card-body">
        <nav className="breadcrumbs" aria-label={`${title} path`}>
          <button type="button" onClick={() => setPath('')}>
            /
          </button>
          {segments.map((segment, index) => (
            <span key={`${segment}-${index}`}>
              <span className="sep">/</span>
              <button type="button" onClick={() => setPath(segments.slice(0, index + 1).join('/'))}>
                {segment}
              </button>
            </span>
          ))}
        </nav>

        <QueryState isLoading={browse.isLoading} error={browse.error}>
          {(browse.data ?? []).length === 0 ? (
            <EmptyState icon="📂" title={path ? 'Empty folder' : 'Nothing here yet'} />
          ) : (
            <div className="browser">
              {browse.data?.map((entry) => (
                <div className="browser-row" key={entry.relative_path}>
                  <div className="name">
                    <span aria-hidden>{entry.is_dir ? '📁' : '📄'}</span>
                    {entry.is_dir ? (
                      <button type="button" onClick={() => setPath(entry.relative_path)}>
                        {entry.name}
                      </button>
                    ) : (
                      <span className="truncate" title={entry.relative_path}>
                        {entry.name}
                      </span>
                    )}
                  </div>
                  <span className="meta">{entry.is_dir ? '' : formatBytes(entry.size_bytes)}</span>
                  <span className="meta">{entry.is_dir ? '' : formatDateTime(entry.modified_at)}</span>
                </div>
              ))}
            </div>
          )}
        </QueryState>
      </div>
    </section>
  );
}
