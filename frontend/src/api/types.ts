export type TransferStatus =
  | 'PENDING'
  | 'PROCESSING'
  | 'SUCCESS'
  | 'FAILED'
  | 'EXCEPTION'
  | 'DUPLICATE_SKIPPED'
  | 'SAM_PENDING';

export type FailureReason =
  | 'NO_MAPPING'
  | 'VALIDATION_ERROR'
  | 'VAULT_API_ERROR'
  | 'RATE_LIMIT'
  | 'CORRUPT_ARCHIVE'
  | 'MISSING_PICKLIST';

export type ResolutionStatus = 'OPEN' | 'RESOLVED' | 'ESCALATED';
export type MatchType = 'EXACT' | 'REGEX' | 'STUDY_DEFAULT' | 'GLOBAL_DEFAULT';

export interface MBoxEntry {
  name: string;
  relative_path: string;
  is_dir: boolean;
  size_bytes: number | null;
  modified_at: string | null;
}

export interface ResolvedMetadata {
  study?: string | null;
  country?: string | null;
  site?: string | null;
  document_type?: string | null;
  document_subtype?: string | null;
  classification?: string | null;
  category?: string | null;
  [key: string]: unknown;
}

export interface Transfer {
  transfer_id: string;
  source_path: string;
  file_name: string;
  file_checksum: string | null;
  file_size_bytes: number | null;
  vault_document_id: string | null;
  mapping_id_used: number | null;
  status: TransferStatus;
  initiated_by: 'SYSTEM' | 'USER';
  message: string | null;
  resolved_metadata: ResolvedMetadata | null;
  attempt_count: number;
  created_at: string | null;
  updated_at: string | null;
  correlation_id: string;
}

export interface TransferSubmitRequest {
  correlation_id?: string;
  files: Array<{
    source_path: string;
    initiated_by?: 'SYSTEM' | 'USER';
    override_duplicate?: boolean;
    metadata_override?: Record<string, unknown> | null;
  }>;
}

export interface TransferSubmitResponse {
  correlation_id: string;
  submitted: number;
  results: Transfer[];
}

export interface Failure {
  failure_id: number;
  transfer_id: string;
  failure_reason: FailureReason;
  failure_detail: string | null;
  suggested_mapping: Record<string, string | null> | null;
  assigned_to: string | null;
  resolution_status: ResolutionStatus;
  created_at: string | null;
  resolved_at: string | null;
}

export interface MappingOverride {
  target_study?: string | null;
  target_country?: string | null;
  target_site?: string | null;
  document_type?: string | null;
  document_subtype?: string | null;
  classification?: string | null;
  default_metadata?: Record<string, unknown> | null;
}

export interface FailureResolveResponse {
  failure_id: number;
  transfer: Transfer;
  resolution_status: ResolutionStatus;
}

export interface MappingRule {
  mapping_id: number;
  source_pattern: string;
  target_study: string | null;
  target_country: string | null;
  target_site: string | null;
  document_type: string | null;
  document_subtype: string | null;
  classification: string | null;
  default_metadata: Record<string, unknown> | null;
  match_type: MatchType;
  priority: number;
  is_active: boolean;
  version: number;
  created_by: string | null;
  created_at: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export type MappingRuleInput = Omit<
  MappingRule,
  'mapping_id' | 'version' | 'created_by' | 'created_at' | 'updated_by' | 'updated_at'
>;

export interface AuditEntry {
  audit_id: number;
  action: string;
  performed_by: string;
  timestamp: string;
  details: Record<string, unknown> | null;
  source_system: string | null;
  correlation_id: string | null;
}

export interface SamAction {
  sam_id: number;
  transfer_id: string;
  missing_fields: Record<string, string>;
  status: 'OPEN' | 'REQUESTED' | 'COMPLETED';
  requested_at: string | null;
  completed_at: string | null;
}

export interface AgingFolder {
  folder_path: string;
  study: string | null;
  age_days: number;
  alert_level: 'OK' | 'WARNING' | 'CRITICAL';
}

export interface DashboardStats {
  transfers_by_status: Partial<Record<TransferStatus, number>>;
  failures_by_reason: Partial<Record<FailureReason, number>>;
  open_failures: number;
  open_sam_items: number;
  mapping_hit_rate: number;
  total_transfers: number;
  documents_in_vault: number;
  aging_folders: AgingFolder[];
  generated_at: string;
}

export type FileRoot = 'source' | 'destination' | 'unclassified';

export interface PocCounts {
  source: number;
  destination: number;
  unclassified: number;
}

export interface PocStatus {
  vault_client: string;
  source_root: string;
  destination_root: string;
  unclassified_root: string;
  rate_limit_per_sec: number;
  unmapped_policy: string;
  zip_max_depth: number;
  counts: PocCounts;
  generated_at: string;
}

export interface PocRunResponse {
  correlation_id: string;
  submitted: number;
  skipped_extension: number;
  skipped_depth: number;
  results: Transfer[];
}

/** A migration job, derived client-side by grouping transfers on their correlation id. */
export interface Job {
  jobId: string;
  correlationId: string;
  study: string;
  status: 'DONE' | 'PARTIAL' | 'RUNNING' | 'FAILED';
  startedAt: string | null;
  updatedAt: string | null;
  initiatedBy: string;
  counts: Record<TransferStatus, number>;
  total: number;
  transfers: Transfer[];
}
