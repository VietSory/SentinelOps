import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-semibold transition-colors cursor-pointer disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary:
          'bg-orange-600 text-white shadow-xs hover:bg-orange-700 active:bg-orange-800',
        danger:
          'bg-red-600 text-white shadow-xs hover:bg-red-700 active:bg-red-800',
        'danger-outline':
          'border border-red-200 bg-red-50/70 text-red-700 hover:bg-red-100 hover:border-red-300',
        secondary:
          'border border-slate-200 bg-white text-slate-800 shadow-2xs hover:bg-slate-50 hover:border-slate-300',
        subtle:
          'bg-orange-50 text-orange-700 border border-orange-200/80 hover:bg-orange-100',
        ghost: 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
        link: 'text-orange-600 underline-offset-4 hover:underline px-0 py-0 h-auto',
      },
      size: {
        sm: 'h-8 px-3 text-xs',
        md: 'h-9 px-4 text-sm',
        lg: 'h-10 px-5 text-sm',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: {
      variant: 'primary',
      size: 'md',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, type = 'button', ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  ),
);
Button.displayName = 'Button';
