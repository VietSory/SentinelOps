import * as React from 'react';
import { cn } from '@/lib/utils';

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  accent?: 'none' | 'orange' | 'red' | 'emerald';
  interactive?: boolean;
}

const accentStyles = {
  none: 'border-slate-200/90 bg-white',
  orange: 'border-orange-200 bg-gradient-to-br from-orange-50/70 via-white to-white ring-1 ring-orange-100/80',
  red: 'border-red-200 bg-gradient-to-br from-red-50/65 via-white to-white ring-1 ring-red-100/80',
  emerald: 'border-emerald-200 bg-gradient-to-br from-emerald-50/50 via-white to-white',
};

export function Card({
  accent = 'none',
  interactive = false,
  className,
  children,
  ...props
}: CardProps) {
  return (
    <div
      className={cn(
        'min-w-0 max-w-full rounded-xl border shadow-[0_1px_2px_rgba(15,23,42,0.04)]',
        accentStyles[accent],
        interactive &&
          'transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[0_10px_24px_-12px_rgba(15,23,42,0.14)] hover:border-orange-300/80',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'min-w-0 flex flex-wrap items-start justify-between gap-3 px-5 pt-5 pb-3 [&>*]:min-w-0',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardTitle({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn('text-base font-semibold tracking-tight text-slate-900 break-words [overflow-wrap:anywhere]', className)}
      {...props}
    >
      {children}
    </h3>
  );
}

export function CardDescription({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p className={cn('text-xs text-slate-500 mt-0.5 break-words [overflow-wrap:anywhere]', className)} {...props}>
      {children}
    </p>
  );
}

export function CardContent({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('min-w-0 px-5 pb-5', className)} {...props}>
      {children}
    </div>
  );
}
