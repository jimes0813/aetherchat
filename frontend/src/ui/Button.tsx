import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';
export type ButtonSize = 'sm' | 'md';

const variantClass: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-accent-contrast hover:opacity-95',
  secondary: 'border border-border bg-paper-raised text-ink hover:bg-accent-soft',
  danger: 'bg-danger text-white hover:opacity-95',
  ghost: 'border border-border bg-transparent text-ink hover:bg-accent-soft',
};

const sizeClass: Record<ButtonSize, string> = {
  sm: 'h-8 rounded-control px-3 text-[13px]',
  md: 'h-10 rounded-control px-4 text-sm',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
}

export function Button({
  variant = 'primary',
  size = 'md',
  className = '',
  type = 'button',
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`inline-flex items-center justify-center font-medium transition disabled:cursor-wait disabled:opacity-60 ${variantClass[variant]} ${sizeClass[size]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
