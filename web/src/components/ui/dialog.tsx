import * as React from 'react';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useLocale } from '@/i18n/LocaleContext';

export interface NativeDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
}

/**
 * Native <dialog> modal following modern-web-guidance:
 * - Opened via showModal() for built-in inert page, focus containment, Esc, and focus return.
 * - Uses closedby="any" with bounding-box pointer fallback where closedBy is unsupported.
 * - Resets dialog.returnValue on close to avoid stale values leaking across openings.
 */
export function NativeDialog({
  open,
  onClose,
  title,
  description,
  children,
  className,
}: NativeDialogProps) {
  const { isEn } = useLocale();
  const ref = React.useRef<HTMLDialogElement>(null);
  const titleId = React.useId();

  React.useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  React.useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;

    const handleClose = () => {
      dialog.returnValue = '';
      onClose();
    };
    dialog.addEventListener('close', handleClose);

    // Fallback for browsers without native closedby="any" light dismiss
    let cleanupBackdrop: (() => void) | undefined;
    if (!('closedBy' in HTMLDialogElement.prototype)) {
      const outside = (e: PointerEvent) => {
        const r = dialog.getBoundingClientRect();
        return (
          e.clientX < r.left ||
          e.clientX > r.right ||
          e.clientY < r.top ||
          e.clientY > r.bottom
        );
      };
      let pressedOutside = false;
      const onDown = (e: PointerEvent) => {
        pressedOutside = outside(e);
      };
      const onUp = (e: PointerEvent) => {
        if (pressedOutside && outside(e) && dialog.open) {
          dialog.close();
        }
      };
      dialog.addEventListener('pointerdown', onDown);
      dialog.addEventListener('pointerup', onUp);
      cleanupBackdrop = () => {
        dialog.removeEventListener('pointerdown', onDown);
        dialog.removeEventListener('pointerup', onUp);
      };
    }

    return () => {
      dialog.removeEventListener('close', handleClose);
      cleanupBackdrop?.();
    };
  }, [onClose]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      {...({ closedby: 'any' } as Record<string, string>)}
    >
      <div
        className={cn(
          'rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl text-slate-900',
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h2 id={titleId} className="text-lg font-semibold text-slate-900">
              {title}
            </h2>
            {description && (
              <p className="mt-1 text-sm text-slate-600">{description}</p>
            )}
          </div>
          <button
            type="button"
            onClick={() => ref.current?.close()}
            aria-label={isEn ? 'Close dialog' : 'Đóng hộp thoại'}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </div>
    </dialog>
  );
}
