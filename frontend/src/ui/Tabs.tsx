import type { ReactNode } from 'react';

export interface TabItem<T extends string> {
  id: T;
  label: string;
  icon?: ReactNode;
}

export interface TabsProps<T extends string> {
  value: T;
  items: TabItem<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
}

export function Tabs<T extends string>({ value, items, onChange, ariaLabel }: TabsProps<T>) {
  return (
    <div role="tablist" aria-label={ariaLabel} className="flex gap-1 rounded-control border border-border bg-paper-sunken p-1">
      {items.map((item) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(item.id)}
            className={`inline-flex flex-1 items-center justify-center gap-1.5 rounded-control px-2 py-1.5 text-xs font-medium transition ${
              selected ? 'bg-accent text-accent-contrast' : 'text-ink-muted hover:bg-accent-soft hover:text-ink'
            }`}
          >
            {item.icon}
            <span>{item.label}</span>
          </button>
        );
      })}
    </div>
  );
}
