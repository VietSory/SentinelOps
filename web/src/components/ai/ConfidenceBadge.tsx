import { useLocale } from '@/i18n/LocaleContext';
import { cn } from '@/lib/utils';
import type { AiExplanationState, Incident } from '@/types/sentinelops';

export interface ConfidenceBadgeProps {
  /** RCA ranking score scaled to 0-100, or null when unavailable. Not a probability. */
  value: number | null;
  /** Where the number comes from: AI explanation, deterministic fallback, AI abstained, or the RCA ranking only. */
  source: AiExplanationState | 'ranking';
  size?: 'sm' | 'lg';
  className?: string;
}

export function incidentConfidence(incident: Incident): Pick<ConfidenceBadgeProps, 'value' | 'source'> {
  const score = incident.aiExplanation?.score;
  return {
    value: score != null ? score * 100 : incident.rcaAbstained ? null : incident.rcaConfidence,
    source: incident.aiExplanation?.state ?? 'ranking',
  };
}

export function ConfidenceBadge({ value, source, size = 'sm', className }: ConfidenceBadgeProps) {
  const { isEn } = useLocale();
  const label = isEn ? 'RCA confidence' : 'Độ tin cậy RCA';
  const note = isEn ? 'ranking score, not a probability' : 'điểm xếp hạng, không phải xác suất';
  const sourceLabel = {
    ai: 'AI',
    fallback: 'Fallback',
    abstained: null,
    ranking: isEn ? 'RCA ranking' : 'Xếp hạng RCA',
  }[source];
  const score = source === 'abstained' || !Number.isFinite(value) ? null : value;
  const result = score === null
    ? isEn ? 'AI abstained' : 'AI chưa kết luận'
    : `${Math.round(score)}%`;
  const displayLabel = [label, result, sourceLabel, note].filter(Boolean).join('. ');
  const tone = score === null
    ? 'bg-[#475569] text-white'
    : score >= 70
      ? 'bg-[#b9380b] text-white'
      : score >= 40
        ? 'bg-[#fcd34d] text-[#422006]'
        : 'bg-[#334155] text-white';

  return (
    <span
      role="group"
      aria-label={displayLabel}
      title={displayLabel}
      data-testid="rca-confidence"
      data-source={source}
      data-size={size}
      className={cn(
        'inline-grid min-w-0 max-w-full gap-1 rounded-lg align-top leading-tight break-words shadow-sm',
        size === 'lg' ? 'px-4 py-3' : 'px-2.5 py-2',
        tone,
        className,
      )}
    >
      <span className="font-semibold text-xs">{label}</span>
      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className={cn(
          'font-extrabold tabular-nums',
          score === null ? 'text-sm' : size === 'lg' ? 'text-4xl' : 'text-2xl',
        )}>
          {result}
        </span>
        {sourceLabel && (
          <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-bold text-slate-900">
            {sourceLabel}
          </span>
        )}
      </span>
      <span className="text-[11px]">{note}</span>
    </span>
  );
}
