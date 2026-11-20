import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';
import { ArrowRight, CheckCircle2, AlertTriangle, ShieldAlert } from 'lucide-react';
import { ConfidenceBadge, incidentConfidence } from '@/components/ai/ConfidenceBadge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge, SeverityBadge, StatusBadge, Keyword } from '@/components/ui/badge';
import { cn, formatDateTime, formatNumber, formatRelativeMinutes } from '@/lib/utils';
import { useLocale } from '@/i18n/LocaleContext';
import { getDashboardTrend } from '@/data';
import type {
  Incident,
  NavSection,
  RemediationProposal,
  ServiceHealthOverview,
} from '@/types/sentinelops';

export interface DashboardViewProps {
  incidents: Incident[];
  remediations: RemediationProposal[];
  services: ServiceHealthOverview[];
  live?: {trend:{slot:string;incidents:number;latencyMs:number|null}[]};
  onOpenIncident: (incidentId: string) => void;
  onNavigate: (section: NavSection, incidentId?: string) => void;
}

export function DashboardView({
  live,
  incidents,
  remediations,
  services,
  onOpenIncident,
  onNavigate,
}: DashboardViewProps) {
  const { locale, isEn } = useLocale();
  const trendData = live ? live.trend : getDashboardTrend(locale);

  const sortedIncidents = [...incidents].sort((first, second) =>
    Date.parse(second.detectedAt || second.startedAt) - Date.parse(first.detectedAt || first.startedAt),
  );
  const openIncidents = sortedIncidents.filter((inc) => inc.status !== 'recovered' && !inc.resolvedAt);
  const latestOpenIncident = openIncidents[0];
  const criticalOpen = openIncidents.filter((i) => i.severity === 'critical');
  const highOrCriticalOpen = openIncidents.filter(
    (i) => i.severity === 'critical' || i.severity === 'high',
  );
  const resolvedCount = incidents.length - openIncidents.length;
  const pendingRemediations = remediations.filter((r) => r.status === 'pending');

  const completedRemediations = remediations.filter(
    (r) => r.status === 'success' || r.status === 'failed',
  );
  const successRemediations = completedRemediations.filter((r) => r.status === 'success');
  const remediationSuccessRate = completedRemediations.length
    ? Math.round((successRemediations.length / completedRemediations.length) * 100)
    : live ? NaN : 100;

  const incidents24h = incidents.filter(
    (inc) => Date.now() - Date.parse(inc.detectedAt || inc.startedAt) <= 24 * 3_600_000,
  ).length;

  const highestLatency = services.length ? Math.max(...services.map((s) => s.p95LatencyMs)) : NaN;
  const avgErrorRate =
    services.reduce((acc, s) => acc + s.errorRatePct, 0) / Math.max(1, services.length);
  const avgSloHealth =
    services.reduce((acc, s) => acc + s.sloSuccessPct, 0) / Math.max(1, services.length);

  const focusServices = Array.from(
    new Set(highOrCriticalOpen.map((i) => i.primaryService)),
  );
  const telemetryKnown=services.length>0&&services.every(s=>Number.isFinite(s.p95LatencyMs)&&Number.isFinite(s.errorRatePct));
  const systemHealthy = highOrCriticalOpen.length === 0 && (!live||(telemetryKnown&&services.every(s=>s.p95LatencyMs<=750&&s.errorRatePct<=5)));

  return (
    <div className="space-y-6">
      {/* Executive Quick Summary Banner */}
      <Card
        accent={systemHealthy ? 'emerald' : criticalOpen.length > 0 ? 'red' : 'orange'}
        className="overflow-hidden"
      >
        <div className="p-5 md:p-6 flex flex-col lg:flex-row lg:items-center justify-between gap-5">
          <div className="space-y-2.5 max-w-3xl">
            <div className="flex flex-wrap items-center gap-2">
              {systemHealthy ? (
                <Badge tone="success" dot>
                  {isEn ? 'Measured services within thresholds' : 'Các dịch vụ đo được trong ngưỡng'}
                </Badge>
              ) : (
                <Badge
                  tone={criticalOpen.length > 0 ? 'critical' : 'high'}
                  dot
                  pulse={criticalOpen.length > 0}
                >
                  {live&&!telemetryKnown?(isEn?'Telemetry incomplete':'Telemetry chưa đủ'):(isEn ? 'Needs Immediate Attention' : 'Cần chú ý ngay')}
                </Badge>
              )}
            </div>

            <h2 className="text-xl md:text-2xl font-bold tracking-tight text-slate-900">
              {systemHealthy
                ? isEn
                  ? 'All core services are operating normally with no high-severity incidents.'
                  : 'Toàn bộ hệ thống đang vận hành ổn định, không còn sự cố mức cao.'
                : isEn
                  ? `${highOrCriticalOpen.length} high-priority incidents active across ${focusServices.join(' & ')}.`
                  : `Hệ thống hiện có ${highOrCriticalOpen.length} sự cố mức cao, tập trung ở ${focusServices.join(' và ')}.`}
            </h2>

            <p className="text-sm leading-relaxed text-slate-600">
              {live ? (isEn ? 'Measured service telemetry and incidents in the loaded feed. Approval and verification are recorded separately.' : 'Telemetry đo từ nguồn thật và incident trong danh sách đã tải. Quyết định duyệt và xác minh hồi phục được ghi riêng.') : systemHealthy ? (
                isEn ? (
                  <>
                    All <Keyword>Latency</Keyword> and <Keyword>Error Rate</Keyword> indicators are within{' '}
                    <Keyword>SLO</Keyword> targets. Recent <Keyword>Remediation</Keyword> actions are verified.
                  </>
                ) : (
                  <>
                    Mọi chỉ số <Keyword>Latency</Keyword> và <Keyword>Error Rate</Keyword> đã trở về dưới ngưỡng{' '}
                    <Keyword>SLO</Keyword>. Các hành động <Keyword>Remediation</Keyword> gần nhất đều đã xác minh thành công.
                  </>
                )
              ) : isEn ? (
                <>
                  Detected <strong className="text-red-700 font-semibold">{criticalOpen.length} Critical Incident</strong>{' '}
                  impacting checkout payments and <Keyword>SLO</Keyword> compliance.{' '}
                  {pendingRemediations.length > 0 ? (
                    <>
                      <strong className="text-orange-700 font-semibold">
                        {pendingRemediations.length} Remediation proposal awaiting approval
                      </strong>{' '}
                      from on-call operator.
                    </>
                  ) : (
                    'Proposed remediations are active and undergoing verification.'
                  )}
                </>
              ) : (
                <>
                  Phát hiện <strong className="text-red-700 font-semibold">{criticalOpen.length} Incident Critical</strong>{' '}
                  ảnh hưởng tới luồng thanh toán và độ trễ <Keyword>SLO</Keyword>.{' '}
                  {pendingRemediations.length > 0 ? (
                    <>
                      Có{' '}
                      <strong className="text-orange-700 font-semibold">
                        {pendingRemediations.length} đề xuất Remediation đang chờ xác nhận
                      </strong>{' '}
                      từ người vận hành.
                    </>
                  ) : (
                    'Các đề xuất khắc phục đã được tiếp nhận và đang theo dõi xác minh.'
                  )}
                </>
              )}
            </p>
          </div>

          <div className="flex min-w-0 flex-wrap items-center gap-2.5">
            {latestOpenIncident && (
              <div className="flex max-w-full min-w-0 flex-col items-start gap-2 rounded-xl border border-orange-500 bg-white p-2.5">
                <ConfidenceBadge {...incidentConfidence(latestOpenIncident)} />
                <Button
                  variant={pendingRemediations.length > 0 ? 'secondary' : 'primary'}
                  size="sm"
                  onClick={() => onOpenIncident(latestOpenIncident.id)}
                >
                  {isEn ? 'View latest active incident' : 'Xem sự cố đang mở mới nhất'}
                </Button>
              </div>
            )}
            {pendingRemediations.length > 0 && (
              <Button
                variant="primary"
                size="lg"
                onClick={() => onNavigate('remediation', pendingRemediations[0].incidentId)}
              >
                {isEn
                  ? `Review Remediation (${pendingRemediations.length})`
                  : `Duyệt Remediation (${pendingRemediations.length})`}
                <ArrowRight className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>
      </Card>

      {/* Primary KPI Grid */}
      <section
        aria-label={isEn ? 'System overview metrics' : 'Chỉ số tổng quan hệ thống'}
        className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-4"
      >
        <Card interactive className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            {isEn ? 'Active Incidents' : 'Incident đang mở'}
          </p>
          <div className="mt-2 flex items-baseline gap-2 tabular-nums">
            <span className="text-3xl font-bold text-slate-900">{openIncidents.length}</span>
            {criticalOpen.length > 0 && (
              <span className="text-xs font-semibold text-red-600 bg-red-50 px-1.5 py-0.5 rounded">
                {criticalOpen.length} Critical
              </span>
            )}
          </div>
          <p className="mt-2 text-xs text-slate-500 tabular-nums">
            {isEn ? 'Resolved: ' : 'Đã xử lý: '}
            <strong className="text-emerald-700">{resolvedCount}</strong> ·{' '}
            {isEn ? `Total ${incidents.length}` : `Tổng ${incidents.length} sự cố`}
          </p>
        </Card>

        <Card interactive className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Alerts / Incidents (24h)
          </p>
          <div className="mt-2 flex items-baseline gap-2 tabular-nums">
            <span className="text-3xl font-bold text-slate-900">{live?'—':34}</span>
            <span className="text-sm font-semibold text-orange-600">
              / {incidents24h} {isEn ? 'incidents' : 'sự cố'}
            </span>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            {live ? (isEn?'Recorded incidents in loaded feed; alert totals unavailable.':'Incident trong danh sách đã tải; chưa có tổng số alert.') : isEn ? (
              <>
                <strong className="text-slate-700">88%</strong> noise reduction via correlation
              </>
            ) : (
              <>
                Giảm nhiễu <strong className="text-slate-700">88%</strong> nhờ gom nhóm tín hiệu
              </>
            )}
          </p>
        </Card>

        <Card interactive accent={highestLatency > 750 ? 'orange' : 'none'} className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            {isEn ? 'Peak Latency p95' : 'Latency p95 cao nhất'}
          </p>
          <div className="mt-2 flex items-baseline gap-2 tabular-nums">
            <span
              className={cn(
                'text-3xl font-bold',
                highestLatency > 750 ? 'text-orange-600' : 'text-slate-900',
              )}
            >
              {formatNumber(highestLatency, 0, locale)}
            </span>
            <span className="text-xs font-medium text-slate-500">ms</span>
          </div>
          <p className="mt-2 text-xs text-slate-600 tabular-nums">
            {isEn ? 'SLO limit: ' : 'Ngưỡng SLO: '}
            <strong>750 ms</strong> ·{' '}
            <span className={highestLatency > 750 ? 'text-orange-700 font-semibold' : 'text-emerald-700'}>
              {!Number.isFinite(highestLatency)?(isEn?'Unknown':'Chưa rõ'):highestLatency > 750
                ? isEn ? 'Breached' : 'Vượt ngưỡng'
                : isEn ? 'Healthy' : 'Trong ngưỡng'}
            </span>
          </p>
        </Card>

        <Card interactive accent={avgErrorRate > 5 ? 'red' : 'none'} className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            {live?'Error Rate & Success (service average)':'Error Rate & SLO Health'}
          </p>
          <div className="mt-2 flex items-baseline gap-2 tabular-nums">
            <span
              className={cn(
                'text-3xl font-bold',
                avgErrorRate > 5 ? 'text-red-600' : 'text-emerald-700',
              )}
            >
              {formatNumber(avgErrorRate, 1, locale)}%
            </span>
            <span className="text-xs font-medium text-slate-500">
              SLO {formatNumber(avgSloHealth, 1, locale)}%
            </span>
          </div>
          <p className="mt-2 text-xs text-slate-500 tabular-nums">
            {isEn ? 'Target success: ' : 'Mục tiêu thành công: '}
            <strong className="text-slate-700">{isEn ? '≥ 99.5%' : '≥ 99,5%'}</strong>
          </p>
        </Card>

        <Card interactive className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Remediation Success
          </p>
          <div className="mt-2 flex items-baseline gap-2 tabular-nums">
            <span className="text-3xl font-bold text-emerald-700">{formatNumber(remediationSuccessRate,0,locale)}%</span>
            <span className="text-xs font-medium text-slate-500">
              ({successRemediations.length}/{completedRemediations.length})
            </span>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            {pendingRemediations.length > 0 ? (
              <span className="text-orange-700 font-semibold">
                {isEn
                  ? `${pendingRemediations.length} awaiting approval`
                  : `${pendingRemediations.length} đề xuất chờ duyệt`}
              </span>
            ) : isEn ? (
              'No pending approvals'
            ) : (
              'Không có đề xuất tồn đọng'
            )}
          </p>
        </Card>
      </section>

      {/* Main Split: 24h Trend Chart + Core Service Health */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <Card className="xl:col-span-7">
          <CardHeader>
            <div>
              <CardTitle>
                {isEn ? '24h Incident & Latency Trend' : 'Xu hướng Incident & Latency trong 24 giờ'}
              </CardTitle>
              <CardDescription>
                {isEn
                  ? 'Detected incidents (orange bars) correlated with peak p95 latency (dark line)'
                  : 'Số lượng sự cố phát hiện (cột cam) đối chiếu với độ trễ p95 cao nhất (đường đậm)'}
              </CardDescription>
            </div>
            <Badge tone="high">SLO threshold · 750 ms</Badge>
          </CardHeader>
          <CardContent>
            <div className="h-[270px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={trendData} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
                  <XAxis dataKey="slot" tick={{ fontSize: 12, fill: '#64748B' }} axisLine={false} tickLine={false} />
                  <YAxis
                    yAxisId="left"
                    allowDecimals={false}
                    tick={{ fontSize: 12, fill: '#64748B' }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    yAxisId="right"
                    orientation="right"
                    unit="ms"
                    tick={{ fontSize: 11, fill: '#94A3B8' }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip
                    contentStyle={{
                      borderRadius: '0.75rem',
                      border: '1px solid #E2E8F0',
                      boxShadow: '0 8px 24px -8px rgba(15,23,42,0.12)',
                      fontSize: '12px',
                    }}
                  />
                  <Bar
                    yAxisId="left"
                    dataKey="incidents"
                    name={isEn ? 'Incidents' : 'Số Incident'}
                    fill="#F97316"
                    radius={[6, 6, 0, 0]}
                    barSize={26}
                  />
                  <Line
                    yAxisId="right"
                    type="monotone"
                    dataKey="latencyMs"
                    name="Latency p95 (ms)"
                    stroke="#0F172A"
                    strokeWidth={2.2}
                    dot={{ r: 3.5, fill: '#EA580C', strokeWidth: 2, stroke: '#FFFFFF' }}
                    activeDot={{ r: 5 }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        {/* Core Services Health */}
        <Card className="xl:col-span-5">
          <CardHeader>
            <div>
              <CardTitle>{isEn ? 'Core Services Health' : 'Tình trạng 3 Service chính'}</CardTitle>
              <CardDescription>
                {isEn
                  ? 'Live p95 Latency, Error Rate, and SLO compliance'
                  : 'Theo dõi trực tiếp Latency p95, Error Rate và độ tuân thủ SLO'}
              </CardDescription>
            </div>
            <Button variant="ghost" size="sm" onClick={() => onNavigate('evidence')}>
              {isEn ? 'Service Map →' : 'Xem Service Map →'}
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {services.map((svc) => {
              const isCritical = svc.status === 'critical';
              const isDegraded = svc.status === 'degraded';
              return (
                <div
                  key={svc.name}
                  className={cn(
                    'rounded-xl border p-3.5 transition-colors',
                    isCritical
                      ? 'border-red-200 bg-red-50/45'
                      : isDegraded
                        ? 'border-orange-200 bg-orange-50/40'
                        : 'border-slate-200 bg-slate-50/40',
                  )}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      {isCritical ? (
                        <ShieldAlert className="h-4 w-4 text-red-600 shrink-0" />
                      ) : isDegraded ? (
                        <AlertTriangle className="h-4 w-4 text-orange-600 shrink-0" />
                      ) : (
                        <CheckCircle2 className={cn('h-4 w-4 shrink-0',svc.status==='unknown'?'text-slate-400':'text-emerald-600')} />
                      )}
                      <span className="font-bold text-slate-900">{svc.name}</span>
                      <span className="font-mono text-[11px] text-slate-400">{svc.version}</span>
                    </div>
                    <Badge tone={isCritical ? 'critical' : isDegraded ? 'high' : 'success'} dot>
                      {svc.statusLabelVi}
                    </Badge>
                  </div>

                  <div className="mt-2.5 grid grid-cols-3 gap-2 text-xs tabular-nums">
                    <div className="rounded-lg bg-white/90 border border-slate-200/70 px-2.5 py-1.5">
                      <span className="block text-[11px] text-slate-500">Latency p95</span>
                      <strong
                        className={cn(
                          'text-sm',
                          svc.p95LatencyMs > svc.sloLatencyTargetMs
                            ? 'text-orange-700'
                            : 'text-slate-900',
                        )}
                      >
                        {formatNumber(svc.p95LatencyMs, 0, locale)} ms
                      </strong>
                    </div>
                    <div className="rounded-lg bg-white/90 border border-slate-200/70 px-2.5 py-1.5">
                      <span className="block text-[11px] text-slate-500">Error Rate</span>
                      <strong
                        className={cn(
                          'text-sm',
                          svc.errorRatePct >= 5
                            ? 'text-red-700'
                            : svc.errorRatePct > 0.5
                              ? 'text-orange-700'
                              : 'text-emerald-700',
                        )}
                      >
                        {formatNumber(svc.errorRatePct, 1, locale)}%
                      </strong>
                    </div>
                    <div className="rounded-lg bg-white/90 border border-slate-200/70 px-2.5 py-1.5">
                      <span className="block text-[11px] text-slate-500">SLO Success</span>
                      <strong className="text-sm text-slate-900">
                        {formatNumber(svc.sloSuccessPct, 1, locale)}%
                      </strong>
                    </div>
                  </div>

                  <p className="mt-2 text-xs text-slate-600">{svc.noteVi}</p>
                </div>
              );
            })}
          </CardContent>
        </Card>
      </div>

      {/* Latest Incidents Summary Table */}
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{isEn ? 'Recent Incidents' : 'Tóm tắt Incident gần nhất'}</CardTitle>
            <CardDescription>
              {isEn
                ? 'Active incidents are outlined in orange. Click a row to open the investigation.'
                : 'Sự cố đang mở có viền cam. Chọn một dòng để xem chi tiết điều tra.'}
            </CardDescription>
          </div>
          <Button variant="secondary" size="sm" onClick={() => onNavigate('incidents')}>
            {isEn ? `View All (${incidents.length})` : `Xem tất cả (${incidents.length})`}
          </Button>
        </CardHeader>
        <CardContent>
          <div
            tabIndex={0}
            role="region"
            aria-label={isEn ? 'Recent incidents table' : 'Bảng tóm tắt sự cố gần nhất'}
            className="table-wrapper border border-slate-200"
          >
            <table className="ops-data-table responsive-stack text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <th scope="col" className="px-4 py-3">Incident</th>
                  <th scope="col" className="px-4 py-3">Service</th>
                  <th scope="col" className="px-4 py-3">Severity</th>
                  <th scope="col" className="px-4 py-3">Status</th>
                  <th scope="col" className="px-4 py-3">
                    {isEn ? 'Suspected Root Cause' : 'Root Cause nghi ngờ'}
                  </th>
                  <th scope="col" className="px-4 py-3 text-right">
                    {isEn ? 'Detected' : 'Phát hiện'}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sortedIncidents.slice(0, 5).map((inc) => {
                  const isActive = inc.status !== 'recovered' && !inc.resolvedAt;
                  const detectedAt = inc.detectedAt || inc.startedAt;
                  return (
                    <tr
                      key={inc.id}
                      data-testid="live-incident-row"
                      onClick={() => onOpenIncident(inc.id)}
                      className={cn(
                        'cursor-pointer transition-colors hover:bg-orange-50/50',
                        isActive && 'outline-2 -outline-offset-2 outline-orange-500',
                        isActive && inc.severity === 'critical' && 'bg-red-50/45',
                      )}
                    >
                      <th scope="row" className="px-4 py-3.5 text-left font-normal">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="max-w-full break-all font-mono text-xs font-semibold text-orange-700">
                            {inc.id}
                          </span>
                          {inc.hasRemediation && isActive && (
                            <Badge tone="high" className="text-[10px] px-1.5 py-0">
                              Remediation
                            </Badge>
                          )}
                        </div>
                        <p className="mt-0.5 font-semibold text-slate-900 line-clamp-1">
                          {inc.title}
                        </p>
                      </th>
                      <td data-label="Service" className="px-4 py-3.5 text-slate-700 font-medium">
                        {inc.services.join(', ')}
                      </td>
                      <td data-label="Severity" className="px-4 py-3.5">
                        <SeverityBadge severity={inc.severity} />
                      </td>
                      <td data-label="Status" className="px-4 py-3.5">
                        <StatusBadge status={inc.status} />
                      </td>
                      <td data-label={isEn ? 'Root Cause' : 'Nguyên nhân'} className="px-4 py-3.5 text-xs text-slate-600 max-w-xs">
                        <div className="min-w-0 space-y-1.5">
                          <ConfidenceBadge {...incidentConfidence(inc)} />
                          <span className="line-clamp-2 break-words">{inc.suspectedRootCause}</span>
                        </div>
                      </td>
                      <td
                        data-label={isEn ? 'Detected' : 'Phát hiện'}
                        className="px-4 py-3.5 text-right text-xs text-slate-500 tabular-nums whitespace-nowrap"
                      >
                        <div className="font-medium text-slate-700">
                          {formatRelativeMinutes(detectedAt, locale)}
                        </div>
                        <div>{formatDateTime(detectedAt, locale)}</div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
