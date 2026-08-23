/**
 * The coverage meter is the signature component. These tests protect the
 * property that makes it meaningful: one tick per requirement, countable.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CoverageMeter } from '@/components/CoverageMeter'

describe('CoverageMeter', () => {
  it('renders one tick per requirement', () => {
    const { container } = render(<CoverageMeter met={8} total={9} />)
    expect(container.querySelectorAll('.tick')).toHaveLength(9)
  })

  it('lights exactly the met requirements', () => {
    const { container } = render(<CoverageMeter met={8} total={9} />)
    expect(container.querySelectorAll('.tick.met')).toHaveLength(8)
  })

  it('shows the ratio, not a percentage', () => {
    render(<CoverageMeter met={8} total={9} />)
    expect(screen.getByText('8')).toBeInTheDocument()
    expect(screen.getByText('/ 9')).toBeInTheDocument()
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
  })

  it('is announced to screen readers as a ratio', () => {
    render(<CoverageMeter met={8} total={9} />)
    expect(screen.getByRole('img')).toHaveAccessibleName(
      '8 of 9 must-have requirements met',
    )
  })

  it('renders partial evidence distinctly from met', () => {
    const items = [
      { text: 'React', verdict: 'met' as const, necessity: 'must_have' as const },
      { text: 'WCAG', verdict: 'partial' as const, necessity: 'must_have' as const },
      { text: 'GraphQL', verdict: 'not_met' as const, necessity: 'must_have' as const },
    ]
    const { container } = render(<CoverageMeter met={1} total={3} items={items} />)
    expect(container.querySelectorAll('.tick.met')).toHaveLength(1)
    expect(container.querySelectorAll('.tick.partial')).toHaveLength(1)
  })

  it('excludes nice-to-haves from the meter', () => {
    const items = [
      { text: 'React', verdict: 'met' as const, necessity: 'must_have' as const },
      { text: 'AWS', verdict: 'met' as const, necessity: 'nice_to_have' as const },
    ]
    const { container } = render(<CoverageMeter met={1} total={1} items={items} />)
    expect(container.querySelectorAll('.tick')).toHaveLength(1)
  })
})
