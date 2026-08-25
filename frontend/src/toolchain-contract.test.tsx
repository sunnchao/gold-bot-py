import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from './App'

// 对应源 gold-bot apps/app-web/tests/toolchain-contract.test.ts(L6 工具链冒烟)。
// 验证 jsdom + testing-library + vitest 工具链可渲染移植后的 Dashboard 组件树。
describe('toolchain contract', () => {
  it('renders the dashboard shell without crashing', () => {
    render(<App />)
    expect(screen.getByText('运营总览')).toBeInTheDocument()
    expect(screen.getByRole('navigation')).toBeInTheDocument()
    expect(screen.getByText('账户')).toBeInTheDocument()
    expect(screen.getByText('审计')).toBeInTheDocument()
  })
})
