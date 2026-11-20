import * as React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from 'recharts';
import { ChevronDown, ChevronRight, Copy, Check } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge, SeverityBadge, StatusBadge, Keyword } from '@/components/ui/badge';
import { OpsTabs, type TabOption } from '@/components/ui/tabs';
import { ServiceDependencyMap } from '@/components/visualizations/ServiceDependencyMap';
import { TraceWaterfall } from '@/components/visualizations/TraceWaterfall';
import { cn, formatDateTime } from '@/lib/utils';
import { useLocale } from '@/i18n/LocaleContext';
import type {
  EvidenceCategory,
  EvidenceItem,
  Incident,
  ServiceHealthOverview,
} from '@/types/sentinelops';

export interface EvidenceTraceViewProps {
  incidents: Incident[];
  selectedIncidentId: string;
  onSelectIncident: (id: string) => void;
  services: ServiceHealthOverview[];
  initialTab?: EvidenceCategory;
}

function EvidenceCard({ item }: { item: EvidenceItem }) {
  const { locale, isEn } = useLocale();
  const [expanded, setExpanded] = React.useState(Boolean(item.highlight));
  const [copied, setCopied] = React.useState(false);

  const copyRef = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(item.sourceRef);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      // Ignore clipboard failure
    }
  };

  return (
    <div
      className={cn(
        'rounded-xl border transition-colors overflow-hidden',
        item.highlight
          ? 'border-orange-300/90 bg-orange-50/35'
          : 'border-slate-200 bg-white',
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        className="w-full text-left px-4 py-3.5 flex items-start justify-between gap-3 hover:bg-white/70 cursor-pointer"
      >
        <div className="flex items-start gap-2.5 min-w-0">
          <span className="mt-1 text-slate-400 shrink-0">
            {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </span>
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <Badge
                tone={
                  item.severity === 'critical'
                    ? 'critical'
                    : item.severity === 'warning'
                      ? 'high'
                      : 'neutral'
                }
              >
                {item.service}
              </Badge>
              {item.highlight && (
                <span className="text-[11px] font-semibold text-orange-700">
                  {isEn ? 'Key Evidence' : 'Bằng chứng trọng yếu'}
                </span>
              )}
              <span className="text-[11px] text-slate-400 tabular-nums">
                {formatDateTime(item.observedAt, locale)}
              </span>
            </div>
            <p className="text-sm font-semibold text-slate-900">{item.title}</p>
            <p className="text-xs text-slate-700 leading-relaxed">{item.annotationVi}</p>
          </div>
        </div>

        {item.metricBadge && (
          <span className="shrink-0 rounded-lg border border-orange-200 bg-white px-2.5 py-1 font-mono text-xs font-semibold text-orange-700">
            {item.metricBadge}
          </span>
        )}
      </button>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden border-t border-slate-200/70 bg-white"
          >
            <div className="p-4 space-y-3">
              <pre className="rounded-lg border border-slate-200 bg-slate-50 p-3 font-mono text-xs text-slate-800 whitespace-pre-wrap break-words leading-relaxed">
                {item.detail}
              </pre>

              {item.metadata && (
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  {Object.entries(item.metadata).map(([k, v]) => (
                    <div
                      key={k}
                      className="rounded-lg border border-slate-100 bg-slate-50/70 px-3 py-2"
                    >
                      <span className="block text-[11px] text-slate-500">{k}</span>
                      <span className="text-xs font-semibold text-slate-900">{v}</span>
                    </div>
                  ))}
                </div>
              )}

              <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-xs">
                <code className="font-mono text-[11px] text-slate-500 break-all">
                  {item.sourceRef}
                </code>
                <button
                  type="button"
                  onClick={copyRef}
                  className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 cursor-pointer"
                >
                  {copied ? (
                    <>
                      <Check className="h-3.5 w-3.5 text-emerald-600" />
                      {isEn ? 'Copied' : 'Đã sao chép'}
                    </>
                  ) : (
                    <>
                      <Copy className="h-3.5 w-3.5 text-slate-400" /> Copy Source Ref
                    </>
                  )}
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function EvidenceTraceView({
  incidents,
  selectedIncidentId,
  onSelectIncident,
  services,
  initialTab = 'traces',
}: EvidenceTraceViewProps) {
  const { isEn } = useLocale();
  const [subTab, setSubTab] = React.useState<EvidenceCategory>(initialTab);

  const incident =
    incidents.find((i) => i.id === selectedIncidentId) ?? incidents[0];

  const counts = React.useMemo(() => {
    const map: Record<EvidenceCategory, number> = {
      traces: incident.traces.length,
      metrics: 0,
      logs: 0,
      deployments: 0,
      signals: 0,
    };
    for (const ev of incident.evidence) {
      if (ev.category !== 'traces') map[ev.category] += 1;
    }
    return map;
  }, [incident]);

  const tabs: TabOption<EvidenceCategory>[] = [
    { id: 'traces', label: 'Traces', count: counts.traces, highlight: incident.traces.some((t) => t.isAnomalous) },
    { id: 'metrics', label: 'Metrics', count: counts.metrics },
    { id: 'logs', label: 'Logs', count: counts.logs },
    { id: 'deployments', label: 'Deployments / Changes', count: counts.deployments },
    { id: 'signals', label: 'Related Signals', count: counts.signals },
  ];

  const highlightedEvidence = incident.evidence.filter((e) => e.highlight);
  const currentTabEvidence = incident.evidence.filter((e) => e.category === subTab);

  return (
    <div className="space-y-6">
      <Card className="p-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {isEn ? 'Selected Incident' : 'Chọn Incident để soi Evidence & Trace'}
            </p>
            <p className="text-xs text-slate-500 mt-0.5">
              {incident.title}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label={isEn ? 'Select incident to inspect evidence' : 'Chọn sự cố cần xem Evidence'}
              value={incident.id}
              onChange={(e) => onSelectIncident(e.target.value)}
              className="h-9 min-w-[270px] rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-900 hover:border-orange-300"
            >
              {incidents.slice(0, 5).map((inc) => (
                <option key={inc.id} value={inc.id}>
                  {inc.id} · {inc.primaryService} ({inc.severity.toUpperCase()})
                </option>
              ))}
            </select>
            <SeverityBadge severity={incident.severity} />
            <StatusBadge status={incident.status} />
          </div>
        </div>
      </Card>

      {highlightedEvidence.length > 0 && (
        <section
          aria-label={isEn ? 'Key evidence highlights' : 'Tóm tắt bằng chứng nổi bật'}
          className="grid grid-cols-1 md:grid-cols-3 gap-4"
        >
          {highlightedEvidence.slice(0, 3).map((ev) => (
            <Card key={ev.id} accent={ev.severity === 'critical' ? 'red' : 'orange'} className="p-4">
              <div className="flex items-center justify-between gap-2 mb-2">
                <Badge tone={ev.severity === 'critical' ? 'critical' : 'high'}>
                  {ev.category.toUpperCase()} · {ev.service}
                </Badge>
                {ev.metricBadge && (
                  <span className="font-mono text-[11px] font-bold text-orange-700">
                    {ev.metricBadge}
                  </span>
                )}
              </div>
              <p className="text-sm font-bold text-slate-900">{ev.title}</p>
              <p className="mt-1.5 text-xs text-slate-700 leading-relaxed">{ev.annotationVi}</p>
            </Card>
          ))}
        </section>
      )}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>
              {isEn ? 'Service Map & Dependency Graph' : 'Service Map & Luồng phụ thuộc (Dependency Graph)'}
            </CardTitle>
            <CardDescription>
              {isEn ? (
                <>
                  Request flow across <Keyword>Checkout</Keyword>, <Keyword>Payment</Keyword>,{' '}
                  <Keyword>Inventory</Keyword> and databases — red/orange paths indicate active bottlenecks
                </>
              ) : (
                <>
                  Luồng đi request giữa <Keyword>Checkout</Keyword>, <Keyword>Payment</Keyword>,{' '}
                  <Keyword>Inventory</Keyword> và cơ sở dữ liệu — nhánh đỏ/cam là điểm nghẽn đang xảy ra
                </>
              )}
            </CardDescription>
          </div>
          <div className="flex items-center gap-3 text-xs text-slate-500">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" /> {isEn ? 'Healthy' : 'Ổn định'}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-orange-500" /> {isEn ? 'Slow / Impacted' : 'Chậm / Ảnh hưởng'}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-red-600" /> {isEn ? 'Error / Bottleneck' : 'Lỗi / Bottleneck'}
            </span>
          </div>
        </CardHeader>
        <CardContent>
          <ServiceDependencyMap incident={incident} services={services} />
        </CardContent>
      </Card>

      <Card>
        <div className="px-5 pt-4">
          <OpsTabs
            label={isEn ? 'Evidence categories' : 'Phân loại bằng chứng'}
            tabs={tabs}
            value={subTab}
            onChange={setSubTab}
          />
        </div>

        <CardContent className="pt-5">
          <motion.div
            key={`${incident.id}-${subTab}`}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.18 }}
            role="tabpanel"
            className="space-y-5"
          >
            {subTab === 'traces' && (
              <>
                <TraceWaterfall traces={incident.traces} />
                {currentTabEvidence.length > 0 && (
                  <div className="space-y-3 pt-2">
                    <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                      {isEn ? 'Extracted Trace Evidence' : 'Bằng chứng Trace đã trích xuất'}
                    </h4>
                    {currentTabEvidence.map((item) => (
                      <EvidenceCard key={item.id} item={item} />
                    ))}
                  </div>
                )}
              </>
            )}

            {subTab === 'metrics' && (
              <>
                <div className="rounded-xl border border-slate-200 bg-slate-50/40 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                    <div>
                      <h4 className="text-sm font-semibold text-slate-900">
                        {isEn
                          ? `Latency p95 vs. Baseline — ${incident.primaryService}`
                          : `Biểu đồ Latency p95 so với mức nền (Baseline) — ${incident.primaryService}`}
                      </h4>
                      <p className="text-xs text-slate-500">
                        {isEn
                          ? 'Red dashed line marks the 750 ms SLO breach threshold'
                          : 'Đường nét đứt màu đỏ là ngưỡng vi phạm SLO (750 ms)'}
                      </p>
                    </div>
                    <Badge tone="high">
                      {isEn ? 'Peak p95: ' : 'Đỉnh p95: '}
                      {incident.metricsSnapshot.p95LatencyMs} ms
                    </Badge>
                  </div>
                  <div className="h-[220px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={incident.metricSeries} margin={{ top: 8, right: 16, left: -10, bottom: 0 }}>
                        <defs>
                          <linearGradient id="orangeLatencyFill" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#F97316" stopOpacity={0.32} />
                            <stop offset="95%" stopColor="#F97316" stopOpacity={0.02} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
                        <XAxis dataKey="time" tick={{ fontSize: 12, fill: '#64748B' }} axisLine={false} tickLine={false} />
                        <YAxis unit="ms" tick={{ fontSize: 11, fill: '#64748B' }} axisLine={false} tickLine={false} />
                        <Tooltip
                          contentStyle={{
                            borderRadius: '0.75rem',
                            border: '1px solid #E2E8F0',
                            fontSize: '12px',
                          }}
                        />
                        <ReferenceLine
                          y={750}
                          stroke="#DC2626"
                          strokeDasharray="4 4"
                          label={{ value: 'SLO 750ms', position: 'insideTopRight', fill: '#DC2626', fontSize: 11 }}
                        />
                        <Area
                          type="monotone"
                          dataKey="p95Ms"
                          name="Latency p95 (ms)"
                          stroke="#EA580C"
                          strokeWidth={2.4}
                          fill="url(#orangeLatencyFill)"
                        />
                        <Area
                          type="monotone"
                          dataKey="baselineMs"
                          name="Baseline (ms)"
                          stroke="#10B981"
                          strokeWidth={1.6}
                          fill="transparent"
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="space-y-3">
                  {currentTabEvidence.length ? (
                    currentTabEvidence.map((item) => <EvidenceCard key={item.id} item={item} />)
                  ) : (
                    <p className="text-sm text-slate-500">
                      {isEn ? 'No additional metric evidence recorded.' : 'Không có bằng chứng Metrics bổ sung.'}
                    </p>
                  )}
                </div>
              </>
            )}

            {subTab !== 'traces' && subTab !== 'metrics' && (
              <div className="space-y-3">
                {currentTabEvidence.length ? (
                  currentTabEvidence.map((item) => <EvidenceCard key={item.id} item={item} />)
                ) : (
                  <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50/70 p-8 text-center">
                    <p className="text-sm font-medium text-slate-700">
                      {isEn
                        ? `No ${subTab.toUpperCase()} evidence captured for this incident`
                        : `Không ghi nhận bằng chứng thuộc nhóm ${subTab.toUpperCase()} cho sự cố này`}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      {isEn
                        ? 'Missing evidence categories directly reduce RCA Coverage to prevent unsafe automated conclusions.'
                        : 'Việc thiếu dữ liệu ở một nhóm (ví dụ Deployments) sẽ được phản ánh trực tiếp vào điểm Coverage của RCA.'}
                    </p>
                  </div>
                )}
              </div>
            )}
          </motion.div>
        </CardContent>
      </Card>
    </div>
  );
}
