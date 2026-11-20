import * as React from 'react';
import { motion } from 'framer-motion';
import { ArrowRight, CheckCircle2, ShieldAlert, AlertTriangle } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge, Keyword, SeverityBadge } from '@/components/ui/badge';
import { cn, formatNumber } from '@/lib/utils';
import { useLocale } from '@/i18n/LocaleContext';
import type { Incident, NavSection } from '@/types/sentinelops';

export interface RcaViewProps {
  incidents: Incident[];
  selectedIncidentId: string;
  onSelectIncident: (id: string) => void;
  onNavigate: (section: NavSection, incidentId?: string) => void;
}

export function RcaView({
  incidents,
  selectedIncidentId,
  onSelectIncident,
  onNavigate,
}: RcaViewProps) {
  const { locale, isEn } = useLocale();
  const incident =
    incidents.find((i) => i.id === selectedIncidentId) ?? incidents[0];
  const [selectedHypothesisId, setSelectedHypothesisId] = React.useState<string>(
    incident.hypotheses[0]?.id ?? '',
  );

  React.useEffect(() => {
    setSelectedHypothesisId(incident.hypotheses[0]?.id ?? '');
  }, [incident]);

  const activeHypothesis =
    incident.hypotheses.find((h) => h.id === selectedHypothesisId) ??
    incident.hypotheses[0];

  const linkedEvidence = activeHypothesis
    ? incident.evidence.filter((e) => activeHypothesis.supportingEvidenceIds.includes(e.id))
    : [];

  return (
    <div className="space-y-6">
      <Card className="p-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {isEn ? 'Root Cause Analysis Target' : 'Chọn sự cố để xem Phân tích nguyên nhân'}
            </p>
            <p className="text-xs text-slate-500 mt-0.5">{incident.title}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label={isEn ? 'Select incident for RCA' : 'Chọn sự cố để xem RCA'}
              value={incident.id}
              onChange={(e) => onSelectIncident(e.target.value)}
              className="h-9 min-w-[270px] rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-900 hover:border-orange-300"
            >
              {incidents.slice(0, 5).map((inc) => (
                <option key={inc.id} value={inc.id}>
                  {inc.id} · {inc.primaryService} ({inc.rcaAbstained ? 'ABSTAIN' : `${inc.rcaConfidence}%`})
                </option>
              ))}
            </select>
            <SeverityBadge severity={incident.severity} />
          </div>
        </div>
      </Card>

      <Card accent={incident.rcaAbstained ? 'none' : 'orange'}>
        <div className="p-6 space-y-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-2 max-w-3xl">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={incident.rcaAbstained ? 'neutral' : 'orange'}>
                  {incident.rcaAbstained
                    ? isEn ? 'RCA Abstained · Safety Guard' : 'RCA Abstained · Chế độ bảo vệ an toàn'
                    : isEn ? 'Primary Suspected Root Cause (#1)' : 'Root Cause nghi ngờ chính (#1)'}
                </Badge>
                <span className="text-xs font-semibold text-slate-500">
                  {isEn ? 'Primary service: ' : 'Service trọng tâm: '}
                  <strong className="text-slate-900">{incident.primaryService}</strong>
                </span>
              </div>

              <h2 className="text-xl md:text-2xl font-bold tracking-tight text-slate-900">
                {incident.suspectedRootCause}
              </h2>

              <p className="text-sm leading-relaxed text-slate-700">
                {incident.rcaAbstained
                  ? incident.rcaAbstainReasonVi
                  : incident.hypotheses[0]?.descriptionVi || incident.shortSummaryVi}
              </p>
            </div>

            <div className="flex flex-col sm:flex-row lg:flex-col gap-2 shrink-0">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => onNavigate('evidence', incident.id)}
              >
                {isEn ? 'Inspect Evidence & Trace' : 'Đối chiếu Evidence & Trace'}
              </Button>
              {incident.hasRemediation && (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => onNavigate('remediation', incident.id)}
                >
                  {isEn ? 'View Remediation' : 'Xem đề xuất Remediation'}
                  <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2 border-t border-orange-200/60">
            <div className="rounded-xl bg-white border border-slate-200/80 p-4 space-y-2">
              <div className="flex items-center justify-between text-xs font-semibold">
                <span className="text-slate-600">
                  {isEn ? 'Confidence Score' : 'Mức độ tin cậy (Confidence Score)'}
                </span>
                <span
                  className={cn(
                    'text-base font-bold tabular-nums',
                    incident.rcaConfidence >= 75 ? 'text-orange-600' : 'text-slate-600',
                  )}
                >
                  {incident.rcaConfidence}%
                </span>
              </div>
              <div className="h-2.5 w-full rounded-full bg-slate-100 overflow-hidden">
                <motion.div
                  key={`conf-${incident.id}`}
                  initial={{ width: 0 }}
                  animate={{ width: `${incident.rcaConfidence}%` }}
                  transition={{ duration: 0.45, ease: 'easeOut' }}
                  className={cn(
                    'h-full rounded-full',
                    incident.rcaConfidence >= 75
                      ? 'bg-gradient-to-r from-orange-500 to-orange-600'
                      : 'bg-slate-400',
                  )}
                />
              </div>
              <p className="text-[11px] text-slate-500">
                {isEn ? 'Remediation eligibility gate: ' : 'Ngưỡng mở khóa đề xuất tự động: '}
                <strong>≥ 75%</strong>
              </p>
            </div>

            <div className="rounded-xl bg-white border border-slate-200/80 p-4 space-y-2">
              <div className="flex items-center justify-between text-xs font-semibold">
                <span className="text-slate-600">
                  {isEn ? 'Evidence Coverage' : 'Độ phủ bằng chứng (Evidence Coverage)'}
                </span>
                <span
                  className={cn(
                    'text-base font-bold tabular-nums',
                    incident.rcaCoverage >= 75 ? 'text-emerald-700' : 'text-amber-700',
                  )}
                >
                  {incident.rcaCoverage}%
                </span>
              </div>
              <div className="h-2.5 w-full rounded-full bg-slate-100 overflow-hidden">
                <motion.div
                  key={`cov-${incident.id}`}
                  initial={{ width: 0 }}
                  animate={{ width: `${incident.rcaCoverage}%` }}
                  transition={{ duration: 0.45, ease: 'easeOut' }}
                  className={cn(
                    'h-full rounded-full',
                    incident.rcaCoverage >= 75 ? 'bg-emerald-500' : 'bg-amber-500',
                  )}
                />
              </div>
              <p className="text-[11px] text-slate-500">
                Trace (25%) + Log (15%) + ML Model (25%) + Deployment Receipt (15%+)
              </p>
            </div>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <Card className="xl:col-span-7">
          <CardHeader>
            <div>
              <CardTitle>
                {isEn ? 'Ranked Root-Cause Hypotheses' : 'Xếp hạng các giả thuyết nguyên nhân'}
              </CardTitle>
              <CardDescription>
                {isEn ? (
                  <>
                    Select a hypothesis to inspect its supporting <Keyword>Evidence</Keyword>
                  </>
                ) : (
                  <>
                    Chọn từng giả thuyết để xem các <Keyword>Evidence</Keyword> hỗ trợ trực tiếp bên phải
                  </>
                )}
              </CardDescription>
            </div>
            <Badge tone="neutral">
              {incident.hypotheses.length} {isEn ? 'hypotheses' : 'giả thuyết'}
            </Badge>
          </CardHeader>
          <CardContent className="space-y-3">
            {incident.hypotheses.length === 0 ? (
              <p className="text-sm text-slate-500">
                {isEn ? 'No scored hypotheses available.' : 'Chưa có giả thuyết được chấm điểm cho sự cố này.'}
              </p>
            ) : (
              incident.hypotheses.map((hyp) => {
                const selected = activeHypothesis?.id === hyp.id;
                return (
                  <button
                    key={hyp.id}
                    type="button"
                    onClick={() => setSelectedHypothesisId(hyp.id)}
                    className={cn(
                      'w-full text-left rounded-xl border p-4 transition-all cursor-pointer',
                      selected
                        ? 'border-orange-400 bg-orange-50/50 ring-1 ring-orange-200'
                        : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/60',
                    )}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span
                          className={cn(
                            'inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold tabular-nums',
                            hyp.isPrimary
                              ? 'bg-orange-600 text-white'
                              : 'bg-slate-200 text-slate-700',
                          )}
                        >
                          #{hyp.rank}
                        </span>
                        <span className="font-bold text-slate-900">{hyp.service}</span>
                        {hyp.isPrimary && (
                          <Badge tone="high" className="text-[10px]">
                            {isEn ? 'Primary Cause' : 'Nguyên nhân chính'}
                          </Badge>
                        )}
                      </div>
                      <span className="font-mono text-sm font-bold tabular-nums text-orange-700">
                        {hyp.confidence}%{' '}
                        <span className="text-xs font-normal text-slate-400">
                          (score {formatNumber(hyp.score, 2, locale)})
                        </span>
                      </span>
                    </div>

                    <p className="mt-2 text-sm font-semibold text-slate-900">{hyp.title}</p>
                    <p className="mt-1 text-xs text-slate-600 leading-relaxed">{hyp.descriptionVi}</p>

                    <div className="mt-3 h-2 w-full rounded-full bg-slate-100 overflow-hidden">
                      <div
                        className={cn(
                          'h-full rounded-full transition-all',
                          hyp.isPrimary ? 'bg-orange-500' : 'bg-slate-400',
                        )}
                        style={{ width: `${hyp.confidence}%` }}
                      />
                    </div>

                    <div className="mt-2.5 flex flex-wrap gap-1.5">
                      {hyp.signalsUsed.map((sig) => (
                        <span
                          key={sig}
                          className="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-600"
                        >
                          {sig}
                        </span>
                      ))}
                    </div>
                  </button>
                );
              })
            )}
          </CardContent>
        </Card>

        <div className="xl:col-span-5 space-y-6">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>
                  {isEn
                    ? `Supporting Evidence ${activeHypothesis ? `(#${activeHypothesis.rank})` : ''}`
                    : `Bằng chứng hỗ trợ ${activeHypothesis ? `giả thuyết #${activeHypothesis.rank}` : ''}`}
                </CardTitle>
                <CardDescription>
                  {isEn
                    ? 'Concrete signals backing this hypothesis'
                    : 'Các tín hiệu cụ thể chứng minh cho nhận định của RCA'}
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent className="space-y-2.5">
              {linkedEvidence.length === 0 ? (
                <p className="text-xs text-slate-500">
                  {isEn ? 'No linked evidence for this hypothesis.' : 'Không có bằng chứng liên kết cho giả thuyết này.'}
                </p>
              ) : (
                linkedEvidence.map((ev) => (
                  <div
                    key={ev.id}
                    className="rounded-xl border border-slate-200 bg-slate-50/50 p-3 space-y-1"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <Badge tone={ev.severity === 'critical' ? 'critical' : 'high'}>
                        {ev.category.toUpperCase()}
                      </Badge>
                      {ev.metricBadge && (
                        <span className="font-mono text-[11px] font-semibold text-orange-700">
                          {ev.metricBadge}
                        </span>
                      )}
                    </div>
                    <p className="text-xs font-semibold text-slate-900">{ev.title}</p>
                    <p className="text-xs text-slate-600 leading-relaxed">{ev.annotationVi}</p>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div>
                <CardTitle>
                  {isEn ? 'Reasoning Timeline' : 'Timeline suy luận (Reasoning Flow)'}
                </CardTitle>
                <CardDescription>
                  {isEn
                    ? 'Step-by-step inference from raw signals to root cause'
                    : 'Chuỗi bước suy luận minh bạch từ tín hiệu thô tới kết luận nguyên nhân'}
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              {incident.reasoningTimeline.length === 0 ? (
                <p className="text-xs text-slate-500">
                  {isEn ? 'No reasoning timeline recorded.' : 'Chưa có timeline suy luận cho sự cố này.'}
                </p>
              ) : (
                <ol className="relative border-l-2 border-orange-200 ml-3 space-y-5">
                  {incident.reasoningTimeline.map((step) => (
                    <li key={step.step} className="pl-5 relative">
                      <span className="absolute -left-[11px] top-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-white border border-orange-300">
                        {step.status === 'verified' ? (
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                        ) : step.status === 'abstained' ? (
                          <ShieldAlert className="h-3.5 w-3.5 text-amber-600" />
                        ) : (
                          <AlertTriangle className="h-3.5 w-3.5 text-orange-600" />
                        )}
                      </span>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-bold text-slate-900">
                          {isEn ? `Step ${step.step}` : `Bước ${step.step}`}: {step.stage}
                        </span>
                        <span className="font-mono text-[11px] text-slate-400">{step.time}</span>
                      </div>
                      <p className="mt-1 text-xs text-slate-600 leading-relaxed">{step.summaryVi}</p>
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
