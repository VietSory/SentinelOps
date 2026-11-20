import * as React from 'react';
import { cn } from '@/lib/utils';

export interface TabOption<T extends string> {
  id: T;
  label: string;
  count?: number;
  highlight?: boolean;
}

export interface OpsTabsProps<T extends string> {
  label: string;
  tabs: TabOption<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}

/**
 * Accessible single-row tablist following modern-web-guidance:
 * - Uses role="tablist" & role="tab" with aria-selected (no aria-controls).
 * - Shape-based active indicator via CSS ::after border-block-end.
 * - ArrowLeft/ArrowRight/Home/End keyboard navigation with scrollIntoView.
 */
export function OpsTabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
  className,
}: OpsTabsProps<T>) {
  const tabRefs = React.useRef<Record<string, HTMLButtonElement | null>>({});

  const selectTab = (next: T, focus = false) => {
    onChange(next);
    const el = tabRefs.current[next];
    if (el) {
      el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      if (focus) el.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent, index: number) => {
    let targetIndex: number | null = null;
    if (e.key === 'ArrowRight') targetIndex = (index + 1) % tabs.length;
    else if (e.key === 'ArrowLeft') targetIndex = (index - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') targetIndex = 0;
    else if (e.key === 'End') targetIndex = tabs.length - 1;

    if (targetIndex !== null) {
      e.preventDefault();
      selectTab(tabs[targetIndex].id, true);
    }
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn('ops-tablist', className)}
    >
      {tabs.map((tab, index) => {
        const selected = tab.id === value;
        return (
          <button
            key={tab.id}
            ref={(el) => {
              tabRefs.current[tab.id] = el;
            }}
            type="button"
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => selectTab(tab.id)}
            onKeyDown={(e) => handleKeyDown(e, index)}
            className="ops-tab"
          >
            <span>{tab.label}</span>
            {typeof tab.count === 'number' && (
              <span
                className={cn(
                  'rounded-full px-1.5 py-0.2 text-[11px] font-semibold tabular-nums',
                  selected
                    ? 'bg-orange-100 text-orange-700'
                    : tab.highlight
                      ? 'bg-red-50 text-red-700'
                      : 'bg-slate-100 text-slate-600',
                )}
              >
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
