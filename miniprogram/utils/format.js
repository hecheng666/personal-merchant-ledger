// 金额格式化：后端返回的是字符串，避免浮点误差
function money(value) {
  const text = String(value === undefined || value === null ? '0' : value)
  const negative = text.startsWith('-')
  const plain = negative ? text.slice(1) : text
  const parts = plain.split('.')
  let integer = parts[0] || '0'
  const decimal = (parts[1] || '00').padEnd(2, '0').slice(0, 2)
  integer = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return (negative ? '-' : '') + integer + '.' + decimal
}

// 待确认对手方：只有收入写收入，只有支出写支出，两边都有就分开写
function amountText(item) {
  const income = Number(item.income_total || 0)
  const expense = Number(item.expense_total || 0)
  if (income > 0 && expense > 0) {
    return '收 ' + money(item.income_total) + ' / 支 ' + money(item.expense_total)
  }
  if (expense > 0) return '支 ' + money(item.expense_total)
  if (income > 0) return '收 ' + money(item.income_total)
  return '—'
}

function today() {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return now.getFullYear() + '-' + month + '-' + day
}

module.exports = { money, amountText, today }
