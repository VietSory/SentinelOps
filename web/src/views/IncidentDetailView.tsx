import { motion } from 'framer-motion';
import { ArrowLeft, ArrowRight, CheckCircle2, AlertCircle } from 'lucide-react';
import { AiRcaPanel } from '@/components/ai/AiRcaPanel';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  Badge,
  Keyword,
  RemediationBadge,
  SeverityBadge,
  StatusBadge,
} from '@/components/ui/badge';
import { TraceWaterfall } from '@/components/visualizations/TraceWaterfall';
import { cn, formatDateTime, formatNumber, formatRelativeMinutes } from '@/lib/utils';
import { useLocale } from '@/i18n/LocaleContext';
import type { Incident, NavSection, RemediationProposal } from '@/types/sentinelops';

export interface IncidentDetailViewProps {
  incident: Incident;
  remediation?: RemediationProposal;
  onBack: () => void;
  onNavigate: (section: NavSection, incidentId?: string) => void;
}

export function IncidentDetailView({
  incident,
  remediation,
  onBack,
  onNavigate,
}: IncidentDetailViewProps) {
  const { locale, isEn } = useLocale();

  return (
    <motion.div
      key={incident.id}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="space-y-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
          {isEn ? 'Back to Incidents' : 'Quay lại danh sách Incident'}
        </Button>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onNavigate('evidence', incident.id)}
          >
            {isEn ? 'Inspect Evidence & Trace' : 'Mở Evidence & Trace chuyên sâu'}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onNavigate('rca', incident.id)}
          >
            {isEn ? 'Root Cause Analysis' : 'Xem Root Cause Analysis'}
          </Button>
          {remediation && (
            <Button
              variant="primary"
              size="sm"
              onClick={() => onNavigate('remediation', incident.id)}
            >
              {isEn ? 'Go to Remediation' : 'Đi tới Remediation'}
              <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      <AiRcaPanel incident={incident} onEvidence={() => onNavigate('evidence', incident.id)} />

      {/* Layer 1: Header */}
      <Card
        accent={
          incident.severity === 'critical' && incident.status !== 'recovered'
            ? 'red'
            : incident.status === 'recovered'
              ? 'emerald'
              : 'orange'
        }
      >
        <div className="p-6 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span data-testid="live-incident-id" className="max-w-full break-all font-mono text-xs font-bold text-orange-700 bg-orange-50 border border-orange-200 px-2.5 py-0.5 rounded-md">
              {incident.id} · rev {incident.revision}
            </span>
            <SeverityBadge severity={incident.severity} />
            <StatusBadge status={incident.status} />
            <Badge tone="neutral">{incident.environment}</Badge>
            <span className="ml-auto text-xs text-slate-500 tabular-nums">
              {isEn ? 'Detected: ' : 'Phát hiện: '}
              <strong>{formatDateTime(incident.detectedAt, locale)}</strong> (
              {formatRelativeMinutes(incident.detectedAt, locale)})
            </span>
          </div>

          <div className="min-w-0">
            <h2 className="text-xl md:text-2xl font-bold tracking-tight text-slate-900 break-words [overflow-wrap:anywhere]">
              {incident.title}
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-slate-700 max-w-4xl break-words [overflow-wrap:anywhere]">
              {incident.shortSummaryVi}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <span className="text-xs font-semibold text-slate-500">
              {isEn ? 'Affected services:' : 'Service bị ảnh hưởng:'}
            </span>
            {incident.services.map((svc) => (
              <span
                key={svc}
                className={cn(
                  'rounded-lg border px-2.5 py-1 text-xs font-semibold',
                  svc === incident.primaryService
                    ? 'border-orange-300 bg-orange-50 text-orange-800'
                    : 'border-slate-200 bg-white text-slate-700',
                )}
              >
                {svc}
                {svc === incident.primaryService && (isEn ? ' · Primary' : ' · Trọng tâm')}
              </span>
            ))}
          </div>
        </div>
      </Card>

      {/* Layer 2: RCA + Impact */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <Card accent="orange" className="lg:col-span-7">
          <CardHeader>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="orange">Root Cause Analysis</Badge>
                {incident.rcaAbstained ? (
                  <Badge tone="neutral">
                    {isEn ? 'Abstained · Low Coverage' : 'Abstain · Thiếu bằng chứng'}
                  </Badge>
                ) : (
                  <span className="text-xs font-semibold text-orange-700 tabular-nums">
                    {isEn
                      ? `Confidence ${formatNumber(incident.rcaConfidence,1,locale)}% · Coverage ${formatNumber(incident.rcaCoverage,1,locale)}%`
                      : `Độ tin cậy ${formatNumber(incident.rcaConfidence,1,locale)}% · Coverage ${formatNumber(incident.rcaCoverage,1,locale)}%`}
                  </span>
                )}
              </div>
              <CardTitle className="mt-2 text-lg">{incident.suspectedRootCause}</CardTitle>
            </div>
            <Button variant="subtle" size="sm" onClick={() => onNavigate('rca', incident.id)}>
              {isEn ? 'Full RCA →' : 'Chi tiết RCA →'}
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {incident.rcaAbstained ? (
              <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-3.5 text-sm text-amber-950 break-words [overflow-wrap:anywhere]">
                {incident.rcaAbstainReasonVi}
              </div>
            ) : (
              incident.hypotheses[0] && (
                <p className="text-sm text-slate-700 leading-relaxed break-words [overflow-wrap:anywhere]">
                  {incident.hypotheses[0].descriptionVi}
                </p>
              )
            )}

            <div className="space-y-1.5">
              <div className="flex flex-wrap justify-between gap-2 text-xs font-medium text-slate-600 tabular-nums">
                <span>{isEn ? 'Confidence Score' : 'Mức độ tin cậy (Confidence)'}</span>
                <strong className="text-orange-700">{formatNumber(incident.rcaConfidence,1,locale)}%</strong>
              </div>
              <div className="h-2 w-full rounded-full bg-orange-100 overflow-hidden">
                <div
                  className={cn(
                    'h-full rounded-full transition-all',
                    incident.rcaAbstained ? 'bg-slate-400' : 'bg-orange-600',
                  )}
                  style={{ width: `${Math.max(0, Math.min(100, Number.isFinite(incident.rcaConfidence) ? incident.rcaConfidence : 0))}%` }}
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="lg:col-span-5">
          <CardHeader>
            <div>
              <CardTitle>{isEn ? 'System Impact' : 'Ảnh hưởng hệ thống (Impact)'}</CardTitle>
              <CardDescription>
                {isEn ? 'Observed metrics during the incident window' : 'Số liệu đo thực tế tại cửa sổ sự cố'}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-slate-700 leading-relaxed break-words [overflow-wrap:anywhere]">{incident.impactVi}</p>

            <div className="grid grid-cols-2 gap-3 tabular-nums">
              <div className="min-w-0 rounded-xl border border-orange-200/80 bg-orange-50/50 p-3">
                <span className="block text-[11px] font-medium text-slate-500">Latency p95</span>
                <strong className="text-lg font-bold text-orange-700">
                  {formatNumber(incident.metricsSnapshot.p95LatencyMs, 0, locale)} ms
                </strong>
                <span className="block text-[11px] text-slate-500">
                  {isEn ? 'Baseline: ' : 'Nền: '}
                  {formatNumber(incident.metricsSnapshot.baselineLatencyMs,1,locale)} ms
                </span>
              </div>
              <div className="rounded-xl border border-red-200/80 bg-red-50/45 p-3">
                <span className="block text-[11px] font-medium text-slate-500">Error Rate</span>
                <strong
                  className={cn(
                    'text-lg font-bold',
                    incident.metricsSnapshot.errorRatePct >= 5 ? 'text-red-700' : 'text-slate-900',
                  )}
                >
                  {formatNumber(incident.metricsSnapshot.errorRatePct, 1, locale)}%
                </strong>
                <span className="block text-[11px] text-slate-500">
                  {isEn ? 'Limit: 5.0%' : 'Ngưỡng: 5,0%'}
                </span>
              </div>
              <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-3">
                <span className="block text-[11px] font-medium text-slate-500">SLO Health</span>
                <strong className="text-lg font-bold text-slate-900">
                  {formatNumber(incident.metricsSnapshot.sloHealthPct, 1, locale)}%
                </strong>
              </div>
              <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-3">
                <span className="block text-[11px] font-medium text-slate-500">
                  {isEn ? 'Affected Requests' : 'Request bị ảnh hưởng'}
                </span>
                <strong className="text-lg font-bold text-slate-900">
                  {formatNumber(incident.metricsSnapshot.affectedRequestsPct, 1, locale)}%
                </strong>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Layer 3: Key Evidence */}
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{isEn ? 'Key Evidence' : 'Bằng chứng chính (Evidence)'}</CardTitle>
            <CardDescription>
              {isEn
                ? 'Top verified signals captured for this incident'
                : 'Tổng hợp các bằng chứng nổi bật nhất đã được kiểm chứng cho sự cố này'}
            </CardDescription>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onNavigate('evidence', incident.id)}
          >
            {isEn
              ? `View All Evidence (${incident.evidence.length}) →`
              : `Xem toàn bộ Evidence (${incident.evidence.length}) →`}
          </Button>
        </CardHeader>
        <CardContent>
          {incident.evidence.length === 0 ? (
            <p className="text-sm text-slate-500">
              {isEn ? 'No detailed evidence stored for this historical incident.' : 'Không có bằng chứng chi tiết cho sự cố lịch sử này.'}
            </p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {incident.evidence.slice(0, 4).map((ev) => (
                <div
                  key={ev.id}
                  className={cn(
                    'min-w-0 rounded-xl border p-4 space-y-2',
                    ev.highlight
                      ? 'border-orange-200 bg-orange-50/40'
                      : 'border-slate-200 bg-white',
                  )}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0 flex flex-wrap items-center gap-2">
                      <Badge tone={ev.severity === 'critical' ? 'critical' : ev.severity === 'warning' ? 'high' : 'neutral'}>
                        {ev.category.toUpperCase()}
                      </Badge>
                      <span className="text-xs font-semibold text-slate-700 break-words">{ev.service}</span>
                    </div>
                    {ev.metricBadge && (
                      <span className="max-w-full break-all font-mono text-[11px] font-semibold text-orange-700 bg-white border border-orange-200 px-2 py-0.5 rounded">
                        {ev.metricBadge}
                      </span>
                    )}
                  </div>
                  <p className="text-sm font-semibold text-slate-900 break-words [overflow-wrap:anywhere]">{ev.title}</p>
                  <p className="text-xs text-slate-600 leading-relaxed break-words [overflow-wrap:anywhere]">{ev.annotationVi}</p>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Layer 4: Trace Timeline */}
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Trace Timeline</CardTitle>
            <CardDescription>
              {isEn
                ? 'End-to-end request flow across services highlighting error and latency bottlenecks'
                : 'Luồng đi của request qua các service — làm nổi bật span lỗi và điểm nghẽn độ trễ'}
            </CardDescription>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onNavigate('evidence', incident.id)}
          >
            {isEn ? 'Open Service Map →' : 'Phóng to & xem Service Map →'}
          </Button>
        </CardHeader>
        <CardContent>
          <TraceWaterfall traces={incident.traces} />
        </CardContent>
      </Card>

      {/* Layer 5: Remediation + Update History */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <Card accent={remediation?.status === 'pending' ? 'orange' : 'none'} className="lg:col-span-7">
          <CardHeader>
            <div>
              <CardTitle>{isEn ? 'Remediation' : 'Khắc phục (Remediation)'}</CardTitle>
              <CardDescription>
                {isEn
                  ? 'Evidence-gated runbook execution with post-action verification'
                  : 'Đề xuất runbook tự động có kiểm soát dựa trên bằng chứng'}
              </CardDescription>
            </div>
            {remediation && <RemediationBadge status={remediation.status} />}
          </CardHeader>
          <CardContent className="space-y-4">
            {remediation ? (
              <>
                <div className="min-w-0 rounded-xl border border-slate-200 bg-white p-4 space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-semibold text-slate-900 break-words [overflow-wrap:anywhere]">{remediation.runbookName}</p>
                    <span className="font-mono text-xs text-slate-500 break-all">{remediation.runbookId}</span>
                  </div>
                  <p className="text-sm text-slate-600 leading-relaxed break-words [overflow-wrap:anywhere]">{remediation.summaryVi}</p>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    variant="primary"
                    onClick={() => onNavigate('remediation', incident.id)}
                  >
                    {remediation.status === 'pending'
                      ? isEn
                        ? 'Open Approval & Execution Panel'
                        : 'Mở khu vực xác nhận & Phê duyệt'
                      : isEn
                        ? 'View Execution & Verification'
                        : 'Xem tiến trình & Kết quả Verify'}
                    <ArrowRight className="h-4 w-4" />
                  </Button>
                </div>
              </>
            ) : (
              <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4 text-sm text-slate-600 flex items-start gap-3">
                <AlertCircle className="h-5 w-5 text-slate-400 shrink-0 mt-0.5" />
                <div>
                  <p className="font-semibold text-slate-800">
                    {isEn ? 'No automated Remediation proposed' : 'Không có đề xuất Remediation tự động'}
                  </p>
                  <p className="mt-1 text-xs leading-relaxed">
                    {isEn ? (
                      <>
                        This incident did not satisfy automation gates (e.g. missing deployment{' '}
                        <Keyword>Evidence</Keyword> or <Keyword>Root Cause Analysis</Keyword> safely abstained). Follow manual runbook procedures.
                      </>
                    ) : (
                      <>
                        Sự cố này không thỏa mãn điều kiện thực thi tự động (ví dụ thiếu{' '}
                        <Keyword>Evidence</Keyword> thay đổi deployment hoặc <Keyword>Root Cause Analysis</Keyword>{' '}
                        chủ động Abstain). Đội vận hành xử lý theo runbook thủ công.
                      </>
                    )}
                  </p>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-5">
          <CardHeader>
            <div>
              <CardTitle>{isEn ? 'Update History' : 'Lịch sử cập nhật'}</CardTitle>
              <CardDescription>
                {isEn ? 'Incident lifecycle revisions' : 'Các mốc thay đổi revision của sự cố'}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ol className="relative border-l border-slate-200 ml-2.5 space-y-5">
              {incident.updateHistory.map((ev) => (
                <li key={ev.id} className="pl-5 relative">
                  <span
                    className={cn(
                      'absolute -left-[7px] top-1 h-3 w-3 rounded-full border-2 border-white',
                      ev.highlight ? 'bg-orange-500' : 'bg-slate-400',
                    )}
                  />
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs font-bold text-slate-900 break-words [overflow-wrap:anywhere]">
                      {ev.stage}{' '}
                      <span className="font-mono font-normal text-slate-400">· rev {ev.revision}</span>
                    </span>
                    <span className="text-[11px] text-slate-400 tabular-nums shrink-0">
                      {formatDateTime(ev.time, locale)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-600 leading-relaxed break-words [overflow-wrap:anywhere]">{ev.summaryVi}</p>
                  <p className="mt-0.5 text-[11px] text-slate-400 inline-flex items-center gap-1 break-all">
                    <CheckCircle2 className="h-3 w-3 shrink-0 text-slate-400" /> {ev.actor}
                  </p>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      </div>
    </motion.div>
  );
}
