import * as React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { cn, formatNumber } from '@/lib/utils';
import { useLocale } from '@/i18n/LocaleContext';
import { Badge } from '@/components/ui/badge';
import type { TraceRecord } from '@/types/sentinelops';

export interface TraceWaterfallProps {
  traces: TraceRecord[];
  compact?: boolean;
  selectedTraceId?: string;
  onSelectTrace?: (id:string)=>void;
}

export function TraceWaterfall({ traces, compact = false,selectedTraceId,onSelectTrace }: TraceWaterfallProps) {
  const { locale, isEn } = useLocale();
  const [selectedIndex, setSelectedIndex] = React.useState(0);
  const [expandedSpanId, setExpandedSpanId] = React.useState<string | null>(null);

  React.useEffect(() => {
    setSelectedIndex(0);
    const firstError = traces[0]?.spans.find((s) => s.status === 'error');
    setExpandedSpanId(firstError?.id ?? null);
  }, [traces]);

  if (!traces.length) {
    return (
      <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50/70 p-8 text-center">
        <p className="text-sm font-medium text-slate-700">
          {isEn ? 'No Trace records captured for this incident' : 'Chưa có bản ghi Trace cho sự cố này'}
        </p>
        <p className="mt-1 text-xs text-slate-500">
          {isEn
            ? 'Incidents with OpenTelemetry / Jaeger traces will display request path and span waterfalls here.'
            : 'Các sự cố có dữ liệu OpenTelemetry / Jaeger sẽ hiển thị đường đi request và waterfall span tại đây.'}
        </p>
      </div>
    );
  }

  const activeTrace = traces.find(t=>t.id===selectedTraceId) ?? traces[selectedIndex] ?? traces[0];
  const baselineTrace = traces.find((t) => t.isBaseline || (!t.source && !t.isAnomalous));
  const maxDuration = Math.max(
    1,
    ...activeTrace.spans.map((span) => span.offsetMs + span.durationMs),
  );
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => Math.round(maxDuration * ratio));

  const deltaMs =
    activeTrace.isAnomalous && baselineTrace
      ? activeTrace.totalDurationMs - baselineTrace.totalDurationMs
      : null;

  return (
    <div className="space-y-4" data-testid="trace-waterfall">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          {traces.map((trace, index) => {
            const selected = selectedTraceId ? trace.id===selectedTraceId : index === selectedIndex;
            return (
              <button
                key={trace.id}
                type="button"
                onClick={() => {
                  setSelectedIndex(index);onSelectTrace?.(trace.id);
                  const err = trace.spans.find((s) => s.status === 'error');
                  setExpandedSpanId(err?.id ?? null);
                }}
                className={cn(
                  'rounded-lg border px-3 py-1.5 text-left text-xs font-medium transition-colors cursor-pointer',
                  selected
                    ? trace.isAnomalous
                      ? 'border-orange-400 bg-orange-50 text-orange-900 shadow-2xs'
                      : 'border-emerald-400 bg-emerald-50 text-emerald-900 shadow-2xs'
                    : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50',
                )}
              >
                <span className="block font-semibold">{trace.label}</span>
                <span className="font-mono text-[11px] opacity-75">
                  Trace {trace.id.slice(0, 12)}…
                </span>
              </button>
            );
          })}
        </div>

        {deltaMs !== null && deltaMs > 0 && (
          <div className="rounded-lg border border-orange-200 bg-orange-50 px-3 py-1.5 text-xs text-orange-900 tabular-nums">
            {isEn ? 'Slower than baseline by ' : 'Chậm hơn baseline '}
            <strong className="font-bold text-orange-700">
              +{formatNumber(deltaMs, 0, locale)} ms
            </strong>{' '}
            ({formatNumber(activeTrace.totalDurationMs / (baselineTrace?.totalDurationMs || 1), 1, locale)}×)
          </div>
        )}
      </div>

      <div
        className={cn(
          'rounded-xl border px-4 py-3 text-sm leading-relaxed',
          activeTrace.isAnomalous
            ? 'border-orange-200 bg-orange-50/70 text-slate-800'
            : 'border-emerald-200 bg-emerald-50/60 text-slate-800',
        )}
      >
        <div className="flex flex-wrap items-center gap-2 mb-1">
          <Badge tone={activeTrace.isAnomalous ? 'critical' : 'success'} dot>
            {activeTrace.isAnomalous
              ? activeTrace.source==='live' ? (isEn?'Recorded error / slow spans':'Span lỗi / chậm đã ghi nhận') : isEn ? 'Anomalous Trace · Bottleneck Detected' : 'Trace bất thường · Có Bottleneck'
              : activeTrace.source==='live' ? (isEn?'Recorded trace':'Trace đã ghi nhận') : isEn ? 'Baseline Trace (Healthy)' : 'Trace mức nền (Healthy)'}
          </Badge>
          <span className="font-mono text-xs text-slate-500 break-all">ID: {activeTrace.id}</span>
          <span className="ml-auto text-xs font-semibold tabular-nums text-slate-700">
            {isEn ? 'Total duration: ' : 'Tổng thời gian: '}
            {formatNumber(activeTrace.totalDurationMs, 0, locale)} ms
          </span>
        </div>
        <p className="text-sm text-slate-700 break-words [overflow-wrap:anywhere]">{activeTrace.summaryVi}</p>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white overflow-hidden">
        <div className="grid grid-cols-1 md:grid-cols-[240px_1fr_130px] items-center gap-3 border-b border-slate-200 bg-slate-50/80 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          <div>Service &amp; Span</div>
          <div className="hidden md:flex items-center justify-between tabular-nums">
            {ticks.map((t) => (
              <span key={t}>{formatNumber(t, 0, locale)} ms</span>
            ))}
          </div>
          <div className="hidden md:block text-right">
            {isEn ? 'Status · Duration' : 'Trạng thái · Độ trễ'}
          </div>
        </div>

        <ol className="divide-y divide-slate-100">
          {activeTrace.spans.map((span) => {
            const leftPct = (span.offsetMs / maxDuration) * 100;
            const widthPct = Math.max(2.5, (span.durationMs / maxDuration) * 100);
            const isExpanded = expandedSpanId === span.id;
            const isError = span.status === 'error';
            const isSlow = span.status === 'slow';

            return (
              <li
                key={span.id}
                className={cn(
                  'transition-colors',
                  isError && 'bg-red-50/45',
                  isSlow && 'bg-orange-50/35',
                )}
              >
                <button
                  type="button"
                  onClick={() => setExpandedSpanId(isExpanded ? null : span.id)}
                  className="w-full text-left px-4 py-3 grid grid-cols-1 md:grid-cols-[240px_1fr_130px] items-center gap-3 hover:bg-slate-50/80 cursor-pointer"
                >
                  <div
                    className="min-w-0 flex items-start gap-1.5"
                    style={{ paddingLeft: `${span.depth * 14}px` }}
                  >
                    <span className="mt-0.5 text-slate-400 shrink-0">
                      {isExpanded ? (
                        <ChevronDown className="h-3.5 w-3.5" />
                      ) : (
                        <ChevronRight className="h-3.5 w-3.5" />
                      )}
                    </span>
                    <div className="min-w-0">
                      <span
                        className={cn(
                          'text-sm font-semibold truncate block',
                          isError ? 'text-red-800' : 'text-slate-900',
                        )}
                      >
                        {span.service}
                      </span>
                      <p className="font-mono text-[11px] text-slate-500 truncate">
                        {span.operation}
                      </p>
                    </div>
                  </div>

                  <div className="relative h-6 rounded-md bg-slate-100/90 overflow-hidden">
                    <motion.div
                      initial={{ scaleX: 0 }}
                      animate={{ scaleX: 1 }}
                      transition={{ duration: 0.35, ease: 'easeOut' }}
                      style={{
                        left: `${leftPct}%`,
                        width: `${widthPct}%`,
                        transformOrigin: 'left center',
                      }}
                      className={cn(
                        'absolute top-1 bottom-1 rounded-sm',
                        isError
                          ? 'bg-gradient-to-r from-red-600 to-orange-500'
                          : isSlow
                            ? 'bg-orange-400'
                            : 'bg-emerald-500',
                      )}
                    />
                  </div>

                  <div className="flex md:flex-col items-center md:items-end justify-between gap-1 tabular-nums">
                    <Badge
                      tone={isError ? 'critical' : isSlow ? 'high' : 'success'}
                      className="text-[11px]"
                    >
                      {span.statusCode}
                    </Badge>
                    <span className="text-xs font-semibold text-slate-800">
                      {formatNumber(span.durationMs, 0, locale)} ms
                      <span className="font-normal text-slate-400">
                        {' '}
                        (+{formatNumber(span.offsetMs, 0, locale)})
                      </span>
                    </span>
                  </div>
                </button>

                <AnimatePresence initial={false}>
                  {isExpanded && !compact && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.18 }}
                      className="overflow-hidden border-t border-slate-100 bg-white/90"
                    >
                      <div className="px-5 py-3 space-y-2">
                        {span.noteVi && (
                          <p
                            className={cn(
                              'text-xs font-medium px-3 py-2 rounded-lg border break-words [overflow-wrap:anywhere]',
                              isError
                                ? 'bg-red-50 border-red-200 text-red-900'
                                : 'bg-orange-50 border-orange-200 text-orange-900',
                            )}
                          >
                            {span.noteVi}
                          </p>
                        )}
                        <div className="flex flex-wrap gap-2">
                          {Object.entries(span.tags).map(([k, v]) => (
                            <span
                              key={k}
                              className="inline-flex max-w-full flex-wrap items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[11px] text-slate-700 break-all"
                            >
                              <span className="text-slate-400">{k}=</span>
                              <span className="font-semibold">{v}</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
