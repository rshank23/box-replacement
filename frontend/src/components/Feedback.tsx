import type { ReactNode } from 'react';

import { ApiError } from '@/api/client';

export function Spinner() {
  return <span className="spinner" role="status" aria-label="Loading" />;
}

export function LoadingBlock({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="loading-block">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({ icon = '📭', title, hint }: { icon?: string; title: string; hint?: string }) {
  return (
    <div className="empty-state">
      <span className="icon" aria-hidden>
        {icon}
      </span>
      <div>{title}</div>
      {hint ? <div style={{ fontSize: 12.5, marginTop: 4 }}>{hint}</div> : null}
    </div>
  );
}

export function Notice({
  tone = 'info',
  icon,
  children,
}: {
  tone?: 'info' | 'warn' | 'danger' | 'ok';
  icon?: string;
  children: ReactNode;
}) {
  const fallback = { info: 'ℹ', warn: '⚠', danger: '⛔', ok: '✔' }[tone];
  return (
    <div className="notice" data-tone={tone}>
      <span aria-hidden>{icon ?? fallback}</span>
      <div>{children}</div>
    </div>
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return 'Unexpected error';
}

export function QueryState({
  isLoading,
  error,
  children,
}: {
  isLoading: boolean;
  error: unknown;
  children: ReactNode;
}) {
  if (isLoading) return <LoadingBlock />;
  if (error) return <Notice tone="danger">{errorMessage(error)}</Notice>;
  return <>{children}</>;
}
