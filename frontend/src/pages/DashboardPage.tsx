import { Link, useNavigate } from 'react-router-dom';

import { useDashboardStats, useJobs } from '@/api/hooks';
import { JobStatusBadge } from '@/components/Badge';
import { EmptyState, QueryState } from '@/components/Feedback';
import { formatDateShort, formatPercent } from '@/lib/format';

function StatCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: number | string;
  hint?: string;
  tone?: 'ok' | 'warn' | 'danger' | 'info';
}) {
  return (
    <div className="card stat-card" data-tone={tone}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint ? <div className="hint">{hint}</div> : null}
    </div>
  );
}

export function DashboardPage() {
  const stats = useDashboardStats();
  const { jobs, isLoading, error } = useJobs();
  const navigate = useNavigate();

  const byStatus = stats.data?.transfers_by_status ?? {};
  const failed = (byStatus.EXCEPTION ?? 0) + (byStatus.FAILED ?? 0);
  const recentJobs = jobs.slice(0, 8);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Migration Overview</h1>
          <p>
            Post-study archival of clinical trial documents from the MBox share into Veeva Vault TMF. Counters
            refresh automatically every 30 seconds.
          </p>
        </div>
        <Link className="btn btn-primary" to="/upload">
          ▶ Trigger New Migration
        </Link>
      </div>

      <QueryState isLoading={stats.isLoading} error={stats.error}>
        <div className="stat-grid">
          <StatCard label="Total" value={stats.data?.total_transfers ?? 0} hint="Files processed" tone="info" />
          <StatCard
            label="Success"
            value={stats.data?.documents_in_vault ?? 0}
            hint="Documents in Vault TMF"
            tone="ok"
          />
          <StatCard label="Failed" value={failed} hint="Awaiting resolution" tone="danger" />
          <StatCard
            label="Unclassified"
            value={(stats.data?.open_failures ?? 0) + (stats.data?.open_sam_items ?? 0)}
            hint="Failure queue + SAM queue"
            tone="warn"
          />
        </div>

        <div className="grid-2" style={{ marginBottom: 16 }}>
          <section className="card">
            <div className="card-head">
              <h2>Mapping coverage</h2>
              <span className="mono">{formatPercent(stats.data?.mapping_hit_rate ?? 0)}</span>
            </div>
            <div className="card-body">
              <div className="meter">
                <span style={{ width: `${Math.round((stats.data?.mapping_hit_rate ?? 0) * 100)}%` }} />
              </div>
              <p className="muted" style={{ marginBottom: 0, marginTop: 10 }}>
                Share of processed files that resolved to an active mapping rule. Everything below this line is
                queued for a human decision rather than uploaded.
              </p>
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>MBox retention watch</h2>
              <Link to="/notifications">View all</Link>
            </div>
            <div className="card-body tight">
              {(stats.data?.aging_folders ?? []).length === 0 ? (
                <EmptyState icon="🗂" title="No folders approaching the 90-day retention limit" />
              ) : (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Study</th>
                        <th>Folder</th>
                        <th>Age</th>
                        <th>Level</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stats.data?.aging_folders.slice(0, 5).map((folder) => (
                        <tr key={folder.folder_path}>
                          <td>{folder.study ?? '—'}</td>
                          <td className="mono truncate">{folder.folder_path}</td>
                          <td>{folder.age_days} d</td>
                          <td>
                            <span className="badge" data-tone={folder.alert_level === 'CRITICAL' ? 'danger' : 'warn'}>
                              {folder.alert_level}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </section>
        </div>
      </QueryState>

      <section className="card">
        <div className="card-head">
          <h2>Recent Jobs</h2>
          <Link to="/audit">View Full Audit Trail</Link>
        </div>
        <div className="card-body tight">
          <QueryState isLoading={isLoading} error={error}>
            {recentJobs.length === 0 ? (
              <EmptyState icon="🚀" title="No migrations yet" hint="Start one from the Upload page." />
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Job ID</th>
                      <th>Study</th>
                      <th>Files</th>
                      <th>Status</th>
                      <th>Date</th>
                      <th>By</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentJobs.map((job) => (
                      <tr
                        key={job.correlationId}
                        data-clickable="true"
                        onClick={() => navigate(`/mapping/${job.correlationId}`)}
                      >
                        <td className="mono">{job.jobId}</td>
                        <td>{job.study}</td>
                        <td>
                          {job.counts.SUCCESS}/{job.total}
                        </td>
                        <td>
                          <JobStatusBadge status={job.status} />
                        </td>
                        <td>{formatDateShort(job.startedAt)}</td>
                        <td>{job.initiatedBy}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </QueryState>
        </div>
      </section>
    </>
  );
}
