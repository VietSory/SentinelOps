export type Severity = 'critical' | 'high' | 'medium' | 'low';

export type IncidentStatus =
  | 'incident'
  | 'degrading'
  | 'recovering'
  | 'recovered'
  | 'escalated';

export type ServiceName = 'Checkout' | 'Payment' | 'Inventory';

export type EnvironmentName = 'Production' | 'Staging';

export type RemediationStatus =
  | 'pending'
  | 'approved'
  | 'running'
  | 'success'
  | 'failed'
  | 'rejected';

export type NavSection =
  | 'dashboard'
  | 'incidents'
  | 'incident-detail'
  | 'evidence'
  | 'rca'
  | 'remediation'
  | 'reports';

export type EvidenceCategory = 'metrics' | 'logs' | 'traces' | 'deployments' | 'signals';

export interface MetricEvidencePoint {
  time: string;
  p95Ms: number;
  baselineMs: number;
  errorRate: number;
}

export interface EvidenceItem {
  id: string;
  category: EvidenceCategory;
  service: ServiceName | 'Payment DB' | 'Inventory DB';
  title: string;
  annotationVi: string;
  detail: string;
  severity: 'critical' | 'warning' | 'normal';
  highlight?: boolean;
  observedAt: string;
  sourceRef: string;
  metricBadge?: string;
  metadata?: Record<string, string>;
}

export interface TraceSpan {
  id: string;
  service: string;
  operation: string;
  offsetMs: number;
  durationMs: number;
  statusCode: string;
  status: 'ok' | 'slow' | 'error';
  depth: number;
  noteVi?: string;
  tags: Record<string, string>;
}

export interface TraceRecord {
  id: string;
  label: string;
  capturedAt: string;
  totalDurationMs: number;
  isAnomalous: boolean;
  summaryVi: string;
  spans: TraceSpan[];
}

export interface RcaHypothesis {
  id: string;
  rank: number;
  service: string;
  title: string;
  descriptionVi: string;
  confidence: number; // 0-100
  score: number; // 0-1
  coverage: number; // 0-100
  isPrimary: boolean;
  supportingEvidenceIds: string[];
  signalsUsed: string[];
}

export interface RcaReasoningStep {
  step: number;
  time: string;
  stage: string;
  summaryVi: string;
  status: 'verified' | 'warning' | 'abstained';
}

export interface RemediationStep {
  order: number;
  title: string;
  command: string;
  status: 'done' | 'running' | 'pending' | 'failed';
  durationSec?: number;
}

export interface VerificationCheck {
  metric: string;
  before: string;
  after: string;
  target: string;
  passed: boolean;
}

export interface RemediationProposal {
  id: string;
  incidentId: string;
  incidentTitle: string;
  service: ServiceName;
  runbookId: string;
  runbookName: string;
  summaryVi: string;
  riskLevel: 'low' | 'medium' | 'high';
  status: RemediationStatus;
  confidence: number;
  createdAt: string;
  expiresAt: string;
  currentImage?: string;
  rollbackImage?: string;
  approvedBy?: string;
  approvedAt?: string;
  decisionReason?: string;
  steps: RemediationStep[];
  verificationChecks: VerificationCheck[];
  verificationSummaryVi?: string;
}

export interface IncidentUpdateEvent {
  id: string;
  revision: number;
  stage: string;
  time: string;
  actor: string;
  summaryVi: string;
  highlight?: boolean;
}

export interface Incident {
  id: string;
  revision: number;
  title: string;
  shortSummaryVi: string;
  impactVi: string;
  severity: Severity;
  status: IncidentStatus;
  services: ServiceName[];
  primaryService: ServiceName;
  environment: EnvironmentName;
  startedAt: string;
  detectedAt: string;
  resolvedAt?: string;
  suspectedRootCause: string;
  rcaConfidence: number; // 0-100
  rcaCoverage: number; // 0-100
  rcaAbstained?: boolean;
  rcaAbstainReasonVi?: string;
  hasRemediation: boolean;
  remediationId?: string;
  metricsSnapshot: {
    p95LatencyMs: number;
    baselineLatencyMs: number;
    errorRatePct: number;
    sloHealthPct: number;
    affectedRequestsPct: number;
  };
  metricSeries: MetricEvidencePoint[];
  evidence: EvidenceItem[];
  traces: TraceRecord[];
  hypotheses: RcaHypothesis[];
  reasoningTimeline: RcaReasoningStep[];
  updateHistory: IncidentUpdateEvent[];
  timingsMin: {
    mttd: number;
    mtti: number;
    mttr: number | null;
  };
}

export interface ServiceHealthOverview {
  name: ServiceName;
  status: 'healthy' | 'degraded' | 'critical';
  statusLabelVi: string;
  p95LatencyMs: number;
  sloLatencyTargetMs: number;
  errorRatePct: number;
  sloSuccessPct: number;
  rps: number;
  activeIncidents: number;
  version: string;
  noteVi: string;
}
