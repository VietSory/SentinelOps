import { CircleHelp, FileSearch, Info, TriangleAlert, type LucideIcon } from 'lucide-react';
import { ConfidenceBadge } from '@/components/ai/ConfidenceBadge';
import { Badge } from '@/components/ui/badge';
import { useLocale } from '@/i18n/LocaleContext';
import { cn, formatVietnamStamp } from '@/lib/utils';
import type { AiExplanationState, Incident } from '@/types/sentinelops';

const stateLabels: Record<AiExplanationState, { vi: string; en: string }> = {
  ai: { vi: 'Do AI phân tích', en: 'AI analysis' },
  fallback: { vi: 'Giải thích xác định (fallback)', en: 'Deterministic fallback' },
  abstained: { vi: 'AI chưa kết luận', en: 'AI abstained' },
};

const narrativeIcons = new Map<string, LucideIcon>([
  ['Vấn đề:', TriangleAlert],
  ['Problem:', TriangleAlert],
  ['Bằng chứng:', FileSearch],
  ['Evidence:', FileSearch],
  ['Chưa chắc chắn:', CircleHelp],
  ['Uncertain:', CircleHelp],
  ['Chưa đủ dữ liệu:', Info],
  ['Not enough data:', Info],
]);

function RcaNarrative({ narrative }: { narrative: string }) {
  const lines = narrative.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const rows = lines.map((line) => {
    const separator = line.indexOf(':');
    const label = line.slice(0, separator + 1);
    const icon = narrativeIcons.get(label);
    return icon
      ? { label, icon, text: line.slice(separator + 1).trim() }
      : { label: '', icon: undefined, text: line };
  });
  const hasLabels = rows.some(({ icon }) => icon);

  return (
    <div data-testid="ai-rca-narrative" className="text-sm leading-relaxed text-slate-800 [overflow-wrap:anywhere]">
      {hasLabels ? (
        <ul className="space-y-2" role="list">
          {rows.map(({ label, icon: Icon, text }, index) => (
            <li
              key={index}
              data-testid="ai-rca-line"
              data-label={label}
              className="rounded-lg border border-slate-200 bg-white/80 px-3 py-2.5"
            >
              {Icon ? (
                <div className="grid gap-x-4 gap-y-1 sm:grid-cols-[10rem_minmax(0,1fr)]">
                  <strong className="flex items-start gap-2 text-slate-900">
                    <Icon aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />
                    {label}
                  </strong>
                  <span>{text}</span>
                </div>
              ) : text}
            </li>
          ))}
        </ul>
      ) : (
        <ul className="list-disc space-y-1.5 pl-5">
          {narrative.split(/(?<=\.)[^\S\r\n]+|;[^\S\r\n]+|\r?\n/)
            .map((sentence) => sentence.trim())
            .filter(Boolean)
            .map((sentence, index) => (
              <li key={index} data-testid="ai-rca-line" data-label="">
                {index === 0 ? <strong>{sentence}</strong> : sentence}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}

export interface AiRcaPanelProps {
  incident: Incident;
  onEvidence?: (ref: string) => void;
  className?: string;
}

export function AiRcaPanel({ incident, onEvidence, className }: AiRcaPanelProps) {
  const { locale, isEn } = useLocale();
  const {
    aiExplanation: explanation,
    aiExplanationStatus: status,
    aiExplanationError: error,
  } = incident;
  const loading = status === 'loading';
  const failed = status === 'error';

  if (!explanation && !loading && !failed) return null;

  const showExplanation = explanation && !loading && !failed;
  const fromAi = showExplanation && explanation.state === 'ai';
  const title = isEn ? 'RCA Explanation' : 'Giải thích nguyên nhân sự cố';
  const chipClass =
    'max-w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 [overflow-wrap:anywhere]';

  return (
    <section
      aria-label={title}
      data-testid="ai-rca-panel"
      className={cn(
        'min-w-0 space-y-4 rounded-2xl border border-l-4 p-4 sm:p-6',
        fromAi
          ? 'border-orange-200 border-l-orange-500 bg-orange-50'
          : 'border-slate-200 border-l-slate-400 bg-slate-50',
        className,
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-2">
          <h2 className="text-lg font-bold tracking-tight text-slate-900">{title}</h2>
          {showExplanation && (
            <Badge data-testid="ai-rca-state" tone={fromAi ? 'orange' : 'neutral'}>
              {stateLabels[explanation.state][locale]}
            </Badge>
          )}
        </div>
        {showExplanation && (
          <ConfidenceBadge
            size="lg"
            value={explanation.score === null ? null : explanation.score * 100}
            source={explanation.state}
          />
        )}
      </div>

      {loading && (
        <div role="status" className="space-y-3">
          <p data-testid="ai-rca-state" className="text-sm font-medium text-slate-700">
            {isEn ? 'Generating explanation…' : 'Đang tạo giải thích…'}
          </p>
          <div aria-hidden="true" className="space-y-2 motion-safe:animate-pulse">
            <div className="h-3 w-full max-w-xl rounded bg-slate-200" />
            <div className="h-3 w-2/3 max-w-md rounded bg-slate-200" />
          </div>
        </div>
      )}

      {failed && (
        <div role="alert" className="space-y-1 text-sm text-slate-700 [overflow-wrap:anywhere]">
          <p data-testid="ai-rca-state" className="font-semibold">
            {isEn ? 'Could not load the RCA explanation.' : 'Không thể tải giải thích nguyên nhân sự cố.'}
          </p>
          {error && <p>{error}</p>}
        </div>
      )}

      {showExplanation && (
        <>
          <p className="text-sm text-slate-700 [overflow-wrap:anywhere]">
            {isEn ? 'Suspected service: ' : 'Dịch vụ nghi vấn: '}
            <strong className="text-slate-900">
              {explanation.service ?? (isEn ? 'Undetermined' : 'Chưa xác định')}
            </strong>
          </p>
          <RcaNarrative narrative={explanation.narrative} />
          {explanation.evidenceRefs.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-semibold text-slate-700">
                {isEn ? 'Evidence references' : 'Tham chiếu bằng chứng'}
              </p>
              <ul className="flex flex-wrap gap-2">
                {[...new Set(explanation.evidenceRefs)].map((ref) => (
                  <li key={ref} className="max-w-full">
                    {onEvidence ? (
                      <button
                        type="button"
                        className={cn(chipClass, 'cursor-pointer text-left hover:border-orange-400 hover:text-orange-800')}
                        onClick={() => onEvidence(ref)}
                      >
                        {ref}
                      </button>
                    ) : (
                      <span className={cn(chipClass, 'inline-block')}>{ref}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-xs text-slate-600">
            {isEn ? 'Generated ' : 'Tạo lúc '}
            <time dateTime={explanation.generatedAt}>
              {formatVietnamStamp(explanation.generatedAt, locale)}
            </time>
          </p>
          <p className="border-t border-slate-300/60 pt-3 text-xs font-semibold text-slate-700">
            {isEn
              ? 'This is the leading hypothesis, not a confirmed root cause.'
              : 'Đây là giả thuyết đang dẫn đầu, chưa phải nguyên nhân đã xác nhận.'}
          </p>
        </>
      )}
    </section>
  );
}
