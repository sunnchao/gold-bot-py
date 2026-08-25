import { useEffect, useState } from 'react'
import { AccountDetailPage } from './AccountDetailPage'

// Vite 对应 gold-bot Next.js app/accounts/[accountId]/page.tsx:
// Next 用 usePathname() 在 hydration 后读取真实 id,这里直接监听 window.location.pathname。
export function AccountRoutePage() {
  const [accountId, setAccountId] = useState('')

  useEffect(() => {
    const update = () => {
      const id = decodeURIComponent(window.location.pathname.split('/').filter(Boolean).at(-1) ?? '')
      setAccountId(id === '__dynamic__' ? '' : id)
    }
    update()
    window.addEventListener('popstate', update)
    return () => {
      window.removeEventListener('popstate', update)
    }
  }, [])

  return <AccountDetailPage accountId={accountId} />
}
