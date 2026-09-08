import { Link } from 'react-router-dom';

import { useDashboardStats, useFailures, useSamQueue } from '@/api/hooks';
import { Badge, ReasonBadge } from '@/components/Badge';
import { EmptyState, QueryState } from '@/components/Feedback';
import { formatDateTime } from '@/lib/format';

export function NotificationsPage() {
  const stats = useDashboardStats();
  const sam = useSamQueue();
  const failures = useFailures({ status: 'OPEN' });

  const aging = stats.data?.aging_folders ?? [];
  const critical = aging.filter((folder) => folder.alert_level === 'CRITICAL');

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Notifications</h1>
          <p>
            Everything that needs a human decision before the 90-day MBox retention window closes. The same signals
            drive the Prometheus alert rules used by operations.
          </p>
        </div>
      </div>

      <div className="stack">
        <section className="card">
          <div className="card-head">
            <h2>MBox retention</h2>
            <Badge tone={critical.length ? 'danger' : aging.length ? 'warn' : 'ok'}>
              {critical.length ? `${critical.length} critical` : aging.length ? `${aging.length} warning` : 'All clear'}
            </Badge>
          </div>
          <div className="card-body tight">
            <QueryState isLoading={stats.isLoading} error={stats.error}>
              {aging.length === 0 ? (
                <EmptyState icon="🗂" title="No study folder is older than 60 days" />
              ) : (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Study</th>
                        <th>Folder</th>
                        <th>Age</th>
                        <th>Level</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {aging.map((folder) => (
                        <tr key={folder.folder_path}>
                          <td>{folder.study ?? '—'}</td>
                          <td className="mono truncate">{folder.folder_path}</td>
                          <td>{folder.age_days} days</td>
                          <td>
                            <Badge tone={folder.alert_level === 'CRITICAL' ? 'danger' : 'warn'}>
                              {folder.alert_level}
                            </Badge>
                          </td>
                          <td>
                            <Link to="/upload">Archive now</Link>
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

        <section className="card">
          <div className="card-head">
            <h2>SAM action queue</h2>
            <Badge tone={sam.data?.length ? 'warn' : 'ok'}>{sam.data?.length ?? 0} open</Badge>
          </div>
          <div className="card-body tight">
            <QueryState isLoading={sam.isLoading} error={sam.error}>
              {(sam.data ?? []).length === 0 ? (
                <EmptyState icon="✅" title="No picklist requests outstanding" />
              ) : (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>SAM ID</th>
                        <th>Missing values</th>
                        <th>Status</th>
                        <th>Requested</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sam.data?.map((item) => (
                        <tr key={item.sam_id}>
                          <td className="mono">#{item.sam_id}</td>
                          <td>
                            {Object.entries(item.missing_fields).map(([field, value]) => (
                              <div key={field}>
                                <span className="muted">{field}:</span> <span className="mono">{value}</span>
                              </div>
                            ))}
                          </td>
                          <td>
                            <Badge tone={item.status === 'COMPLETED' ? 'ok' : 'warn'}>{item.status}</Badge>
                          </td>
                          <td>{formatDateTime(item.requested_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </QueryState>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>Open exceptions</h2>
            <Link to="/unclassified">Resolve</Link>
          </div>
          <div className="card-body tight">
            <QueryState isLoading={failures.isLoading} error={failures.error}>
              {(failures.data ?? []).length === 0 ? (
                <EmptyState icon="✅" title="The failure queue is empty" />
              ) : (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Failure</th>
                        <th>Reason</th>
                        <th>Detail</th>
                        <th>Raised</th>
                      </tr>
                    </thead>
                    <tbody>
                      {failures.data?.slice(0, 20).map((failure) => (
                        <tr key={failure.failure_id}>
                          <td className="mono">#{failure.failure_id}</td>
                          <td>
                            <ReasonBadge reason={failure.failure_reason} />
                          </td>
                          <td className="truncate" title={failure.failure_detail ?? ''}>
                            {failure.failure_detail ?? '—'}
                          </td>
                          <td>{formatDateTime(failure.created_at)}</td>
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
    </>
  );
}
