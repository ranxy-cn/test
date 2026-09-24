// 统一将时间戳展示为北京时间（Asia/Shanghai, UTC+8）
const TZ = 'Asia/Shanghai'

export function fmtTime(value) {
  if (!value) return '-'
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  // sv-SE 输出固定为 YYYY-MM-DD HH:mm:ss，配合 timeZone 强制按北京时间渲染
  return d.toLocaleString('sv-SE', { timeZone: TZ, hour12: false })
}

// Element Plus 表格列 formatter：:formatter="fmtTimeCol('created_at')"
export function fmtTimeCol(prop) {
  return (row) => fmtTime(row[prop])
}

export { TZ }
