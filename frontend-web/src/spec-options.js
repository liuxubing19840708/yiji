// 与小程序编菲保持一致的常用规格池；厂内历史值和订单色码在页面前置合并。
export const COMMON_COLORS = [
  '黑色', '白色', '灰色', '红色', '蓝色', '绿色', '黄色', '橙色',
  '紫色', '粉色', '棕色', '咖啡', '军绿', '卡其', '米色', '花色',
]

export const COMMON_SIZES = [
  'S', 'M', 'L', 'XL', 'XXL', '3XL', '4XL', 'F', '均码',
  '80', '90', '95', '100', '110', '120', '130', '140', '150', '160',
]

export function uniqueOptions(...groups) {
  const seen = new Set()
  const result = []
  for (const group of groups) {
    for (const value of group || []) {
      const name = String(value ?? '').trim()
      if (!name || seen.has(name)) continue
      seen.add(name)
      result.push(name)
    }
  }
  return result
}
