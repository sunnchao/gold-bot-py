export function formatMoney(value: number, currency = 'USD') {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2
  }).format(value)
}

export function formatNumber(value: number) {
  return new Intl.NumberFormat('zh-CN', {
    maximumFractionDigits: 2
  }).format(value)
}

export function formatPercent(value: number) {
  return `${(value * 100).toFixed(2)}%`
}

export function formatTimestamp(value: string) {
  if (!value) {
    return '无'
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short'
  }).format(parsed)
}
