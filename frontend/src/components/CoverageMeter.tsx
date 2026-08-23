/**
 * The segmented coverage meter — the signature component.
 *
 * One tick per must-have requirement, so coverage reads as countable rather
 * than as a continuous percentage. This is deliberate: a bar filled to 89%
 * invites the reader to treat it as a measurement, while eight of nine lit
 * segments makes the missing one the thing you notice.
 *
 * Do not replace this with a progress ring.
 */

import type { CoverageItem } from '@/api/types'

interface Props {
  met: number
  total: number
  /** Optional per-requirement detail so partial evidence renders amber. */
  items?: Pick<CoverageItem, 'text' | 'verdict' | 'necessity'>[]
  size?: 'sm' | 'md' | 'lg'
  showRatio?: boolean
}

export function CoverageMeter({ met, total, items, size = 'md', showRatio = true }: Props) {
  const musts = items?.filter((i) => i.necessity === 'must_have')

  const ticks = musts
    ? musts.map((item, index) => ({
        key: index,
        state: item.verdict === 'met' ? 'met' : item.verdict === 'partial' ? 'partial' : '',
        label: `${item.text}: ${
          item.verdict === 'met' ? 'evidence found'
            : item.verdict === 'partial' ? 'partial evidence'
            : 'no evidence found'
        }`,
      }))
    : Array.from({ length: total }, (_, index) => ({
        key: index,
        state: index < met ? 'met' : '',
        label: index < met ? 'Requirement met' : 'No evidence found',
      }))

  const sizeClass = size === 'sm' ? 'sm' : size === 'lg' ? 'lg' : ''

  return (
    <div className="cov">
      <div
        className={`meter ${sizeClass}`}
        role="img"
        aria-label={`${met} of ${total} must-have requirements met`}
      >
        {ticks.map((tick) => (
          <span key={tick.key} className={`tick ${tick.state}`} title={tick.label} />
        ))}
      </div>
      {showRatio && (
        <span className="cov-ratio">
          {met}
          <span className="den"> / {total}</span>
        </span>
      )}
    </div>
  )
}
