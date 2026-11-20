import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useLocale } from '@/i18n/LocaleContext';
import { cn } from '@/lib/utils';

export const INCIDENTS_PER_PAGE = 5;

export interface PaginationProps {
  currentPage: number;
  totalItems: number;
  pageSize?: number;
  onPageChange: (page: number) => void;
  itemLabelVi?: string;
  itemLabelEn?: string;
  className?: string;
}

function buildPageNumbers(current: number, total: number): (number | 'ellipsis')[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, idx) => idx + 1);
  }
  const pages: (number | 'ellipsis')[] = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);

  if (start > 2) pages.push('ellipsis');
  for (let p = start; p <= end; p += 1) pages.push(p);
  if (end < total - 1) pages.push('ellipsis');
  pages.push(total);
  return pages;
}

export function Pagination({
  currentPage,
  totalItems,
  pageSize = INCIDENTS_PER_PAGE,
  onPageChange,
  itemLabelVi = 'sự cố',
  itemLabelEn = 'incidents',
  className,
}: PaginationProps) {
  const { isEn } = useLocale();
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const safePage = Math.min(Math.max(1, currentPage), totalPages);

  if (totalItems === 0) return null;

  const startItem = (safePage - 1) * pageSize + 1;
  const endItem = Math.min(totalItems, safePage * pageSize);
  const pages = buildPageNumbers(safePage, totalPages);

  return (
    <nav
      aria-label={isEn ? 'Pagination' : 'Phân trang'}
      data-testid="pagination"
      className={cn(
        'flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-xs text-slate-600',
        className,
      )}
    >
      <span className="tabular-nums text-slate-600">
        {isEn
          ? `Showing ${startItem}–${endItem} of ${totalItems} ${itemLabelEn} (Page ${safePage}/${totalPages})`
          : `Hiển thị ${startItem}–${endItem} / ${totalItems} ${itemLabelVi} (Trang ${safePage}/${totalPages})`}
      </span>

      <div className="flex flex-wrap items-center gap-1">
        <button
          type="button"
          onClick={() => onPageChange(safePage - 1)}
          disabled={safePage <= 1}
          aria-label={isEn ? 'Previous page' : 'Trang trước'}
          className="inline-flex h-8 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">{isEn ? 'Prev' : 'Trước'}</span>
        </button>

        {pages.map((entry, idx) =>
          entry === 'ellipsis' ? (
            <span
              key={`ellipsis-${idx}`}
              aria-hidden="true"
              className="px-1.5 text-slate-400 select-none"
            >
              …
            </span>
          ) : (
            <button
              key={entry}
              type="button"
              onClick={() => onPageChange(entry)}
              aria-current={entry === safePage ? 'page' : undefined}
              aria-label={isEn ? `Page ${entry}` : `Trang ${entry}`}
              data-testid={`pagination-page-${entry}`}
              className={cn(
                'inline-flex h-8 min-w-8 items-center justify-center rounded-lg border px-2 text-xs font-semibold tabular-nums transition-colors cursor-pointer',
                entry === safePage
                  ? 'border-orange-600 bg-orange-600 text-white shadow-2xs'
                  : 'border-slate-200 bg-white text-slate-700 hover:border-orange-300 hover:bg-orange-50/60',
              )}
            >
              {entry}
            </button>
          ),
        )}

        <button
          type="button"
          onClick={() => onPageChange(safePage + 1)}
          disabled={safePage >= totalPages}
          aria-label={isEn ? 'Next page' : 'Trang sau'}
          className="inline-flex h-8 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer"
        >
          <span className="hidden sm:inline">{isEn ? 'Next' : 'Sau'}</span>
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </nav>
  );
}
