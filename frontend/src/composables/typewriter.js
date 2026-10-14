// 打字机平滑器：网络 chunk 先进缓冲，requestAnimationFrame 逐帧匀速上屏。
// LLM 网关的增量到达天然是突发的（停顿后一次涌出一大块），直接渲染会一顿一顿；
// 经缓冲后肉眼看到的是匀速流出。步长自适应：积压越多吐越快（约半秒追平），
// chunk 匀速到达时保持匀速打出。流结束时 flush() 把剩余内容一次上屏。
export function createTyper(apply) {
  let buffer = ''
  let raf = 0
  const tick = () => {
    raf = 0
    if (!buffer) return
    const step = Math.max(2, Math.ceil(buffer.length / 30))
    apply(buffer.slice(0, step))
    buffer = buffer.slice(step)
    if (buffer) raf = requestAnimationFrame(tick)
  }
  return {
    push(text) {
      if (!text) return
      buffer += text
      if (!raf) raf = requestAnimationFrame(tick)
    },
    flush() {
      if (raf) {
        cancelAnimationFrame(raf)
        raf = 0
      }
      if (!buffer) return
      const rest = buffer
      buffer = ''
      apply(rest)
    },
  }
}
