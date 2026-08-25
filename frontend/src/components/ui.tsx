import type { ReactNode } from 'react'
import { cx } from '../lib/utils'

type PanelProps = {
  title: string
  subtitle?: string
  className?: string
  children: ReactNode
}

const toneClasses: Record<string, string> = {
  green: 'bg-emerald-400/12 text-emerald-200 ring-1 ring-emerald-300/25',
  amber: 'bg-amber-400/12 text-amber-100 ring-1 ring-amber-300/25',
  blue: 'bg-sky-400/12 text-sky-100 ring-1 ring-sky-300/25',
  orange: 'bg-orange-400/12 text-orange-100 ring-1 ring-orange-300/25',
  red: 'bg-rose-400/12 text-rose-100 ring-1 ring-rose-300/25',
  neutral: 'bg-stone-200/10 text-stone-100 ring-1 ring-white/10'
}

export function Panel({ title, subtitle, className, children }: PanelProps) {
  return (
    <section className={cx('glass-panel rounded-[28px] p-5 sm:p-6', className)}>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-stone-50">{title}</h2>
          {subtitle ? <p className="mt-1 text-sm text-stone-400">{subtitle}</p> : null}
        </div>
      </div>
      {children}
    </section>
  )
}

export function ToneBadge({ tone = 'neutral', children }: { tone?: string; children: ReactNode }) {
  return (
    <span
      className={cx(
        'inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em]',
        toneClasses[tone] ?? toneClasses.neutral
      )}
    >
      {children}
    </span>
  )
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-white/10 bg-black/10 px-4 py-8 text-center">
      <p className="text-sm font-semibold uppercase tracking-[0.24em] text-stone-300">{title}</p>
      <p className="mt-2 text-sm text-stone-500">{detail}</p>
    </div>
  )
}

export function JsonPreview({ value }: { value: unknown }) {
  return (
    <pre className="overflow-x-auto rounded-2xl bg-black/25 p-4 text-xs leading-6 text-stone-200">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}
