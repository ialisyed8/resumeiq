/**
 * Primitives that map 1:1 onto the ported design-system classes.
 * These wrap the CSS rather than reimplementing it, so the prototype's
 * appearance and motion carry across unchanged.
 */

import { useEffect, useRef, type ReactNode } from 'react'
import type { CoverageTier } from '@/api/types'

export const TIER_COLOR: Record<CoverageTier, string> = {
  meets_all: 'var(--green)',
  one_short: 'var(--amber)',
  multiple_gaps: 'var(--slate)',
}

export const TIER_BADGE: Record<CoverageTier, string> = {
  meets_all: 'b-green',
  one_short: 'b-amber',
  multiple_gaps: 'b-slate',
}

type ButtonVariant = 'pri' | 'sec' | 'ghost' | 'danger'

export function Button({
  variant = 'sec', size, children, className = '', ...rest
}: {
  variant?: ButtonVariant
  size?: 'sm' | 'lg'
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const classes = ['btn', `btn-${variant}`, size ? `btn-${size}` : '', className]
    .filter(Boolean).join(' ')
  return <button className={classes} {...rest}>{children}</button>
}

export function Card({ children, pad, className = '' }: {
  children: ReactNode; pad?: boolean; className?: string
}) {
  return <div className={`card ${pad ? 'card-pad' : ''} ${className}`}>{children}</div>
}

export function Badge({ tone = 'slate', children }: {
  tone?: 'green' | 'amber' | 'red' | 'blue' | 'slate'
  children: ReactNode
}) {
  return <span className={`badge b-${tone}`}>{children}</span>
}

export function SectionHead({ title, sub, action }: {
  title: string; sub?: string; action?: ReactNode
}) {
  return (
    <div className="sec-head">
      <div>
        <h3>{title}</h3>
        {sub && <div className="sub">{sub}</div>}
      </div>
      {action}
    </div>
  )
}

export function Skeleton({ width, height = 12, radius = 5 }: {
  width: number | string; height?: number; radius?: number
}) {
  return (
    <div className="sk" style={{
      width: typeof width === 'number' ? `${width}px` : width,
      height: `${height}px`,
      borderRadius: `${radius}px`,
    }} />
  )
}

export function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <div className="sk-row" key={i}>
          <Skeleton width={34} height={34} radius={8} />
          <div style={{ flex: 1 }}>
            <Skeleton width={140 + ((i * 37) % 110)} height={12} />
            <div style={{ height: 7 }} />
            <Skeleton width={180 + ((i * 53) % 100)} height={9} />
          </div>
          <Skeleton width={96} height={8} />
          <Skeleton width={64} height={26} />
        </div>
      ))}
    </>
  )
}

export function EmptyState({ icon, title, body, action }: {
  icon?: ReactNode; title: string; body?: string; action?: ReactNode
}) {
  return (
    <div className="empty">
      {icon && <div className="empty-ico">{icon}</div>}
      <h4>{title}</h4>
      {body && <p>{body}</p>}
      {action}
    </div>
  )
}

export function ErrorState({ title, detail, onRetry }: {
  title: string; detail?: string; onRetry?: () => void
}) {
  return (
    <div className="err-box">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
        <path d="M12 8v5M12 16.5v.01" /><circle cx="12" cy="12" r="9.5" />
      </svg>
      <div style={{ flex: 1 }}>
        <div className="err-t">{title}</div>
        {detail && <div className="err-d">{detail}</div>}
        {onRetry && (
          <div style={{ marginTop: 10 }}>
            <Button size="sm" onClick={onRetry}>Try again</Button>
          </div>
        )}
      </div>
    </div>
  )
}

/** Modal with focus trapping and Escape-to-close. */
export function Modal({ title, children, footer, onClose, wide }: {
  title: string; children: ReactNode; footer?: ReactNode
  onClose: () => void; wide?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    ref.current?.querySelector<HTMLElement>('button, input, textarea, select')?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key !== 'Tab' || !ref.current) return
      const focusable = ref.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      previous?.focus()
    }
  }, [onClose])

  return (
    <div className="scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="modal" role="dialog" aria-modal="true" aria-label={title}
        ref={ref} style={wide ? { maxWidth: 720 } : undefined}
      >
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

export function Drawer({ title, children, footer, onClose }: {
  title: string; children: ReactNode; footer?: ReactNode; onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <div className="drawer" role="dialog" aria-label={title}>
        <div className="sec-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer && <div className="drawer-foot">{footer}</div>}
      </div>
    </>
  )
}
