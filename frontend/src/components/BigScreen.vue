<template>
  <div class="screen-page" :class="{ 'is-standalone': standalone }">
    <div class="screen-stage">
      <!-- ===== 全屏 3D 资产拓扑背景层（主角，中央不遮挡） ===== -->
      <div class="topo-backdrop">
        <AssetTopology3D :mothers="topology" :dark="isDark" bare :highlight-key="highlightKey" :scores="scoreMap" />
      </div>

      <!-- ===== HUD 悬浮层：中央 pointer-events 穿透，数据面板环布四周 ===== -->
      <div class="hud">
        <div v-if="standalone" class="corner-tools">
          <button class="ghost-btn" type="button" :title="isDark ? '切换白天模式' : '切换夜间模式'" @click="toggleTheme">
            {{ isDark ? '☀ 白天' : '🌙 夜间' }}
          </button>
        </div>

        <!-- 顶部 KPI 甲板：一枚超薄玻璃胶囊 -->
        <header class="kpi-dock">
          <span class="dock-brand"><i class="live-dot" />小维 · 值守中</span>
          <em class="dock-sep" />
          <div class="dock-item" title="MTTR = 已恢复工单从立案到恢复的平均时长（分钟）">
            <small>平均恢复 MTTR</small>
            <b>{{ mttrText }}<i>min</i></b>
          </div>
          <em class="dock-sep" />
          <div class="dock-item" title="自动化闭环率 = 无需人工审批即自动恢复的工单 / 全部已恢复工单">
            <small>自动化闭环率</small>
            <b>{{ rateText }}<i>%</i></b>
          </div>
          <em class="dock-sep" />
          <div class="dock-item" title="当前未进入终态（未恢复/未升级）的工单数">
            <small>进行中工单</small>
            <b>{{ automation.open ?? 0 }}</b>
          </div>
          <em class="dock-sep" />
          <div class="dock-item" title="近 7 日异常告警总数">
            <small>近 7 日告警</small>
            <b>{{ alerts.total_7d ?? 0 }}</b>
          </div>
        </header>

        <main class="hud-body">
          <!-- 左栏：效能叙事 -->
          <aside class="hud-col">
            <section class="glass-panel">
              <header class="p-head">恢复效能</header>
              <div class="hero">
                <b>{{ mttrText }}<small>min</small></b>
                <span>平均恢复时长 MTTR</span>
              </div>
              <div class="stat-rows">
                <div class="stat-row"><span>平均响应 MTTA</span><b>{{ mtta.avg_minutes ?? '—' }} min</b></div>
                <div class="stat-row"><span>P50 恢复时长</span><b>{{ p50Text }} min</b></div>
                <div class="stat-row"><span>累计闭环工单</span><b>{{ mttr.recovered_count ?? 0 }} 单</b></div>
                <div class="stat-row"><span>折算节省人工</span><b class="tone-ok">{{ mttr.saved_hours ?? 0 }} h</b></div>
              </div>
            </section>

            <section class="glass-panel">
              <header class="p-head">自动化闭环</header>
              <div class="auto-wrap">
                <div class="ring-wrap">
                  <svg viewBox="0 0 84 84" class="ring">
                    <circle cx="42" cy="42" r="36" class="ring-track" />
                    <circle
                      cx="42" cy="42" r="36" class="ring-bar"
                      :style="{ strokeDasharray: RING_C, strokeDashoffset: RING_C * (1 - ringRate / 100) }"
                    />
                  </svg>
                  <div class="ring-center">
                    <b>{{ rateText }}<small>%</small></b>
                  </div>
                </div>
                <div class="stat-rows auto-rows">
                  <div class="stat-row"><span><i class="dot ok" />全自动恢复</span><b>{{ automation.auto_recovered ?? 0 }} 单</b></div>
                  <div class="stat-row"><span><i class="dot warn" />审批后恢复</span><b>{{ automation.approved_recovered ?? 0 }} 单</b></div>
                  <div class="stat-row"><span><i class="dot danger" />升级人工</span><b>{{ automation.escalated ?? 0 }} 单</b></div>
                  <div class="stat-row"><span>AI 建议采纳率</span><b>{{ aiStats.adoption_rate ?? '—' }}<template v-if="aiStats.adoption_rate != null">%</template></b></div>
                  <div class="stat-row"><span>审批均等待</span><b>{{ approval.avg_wait_minutes ?? '—' }} min</b></div>
                  <div v-if="approval.expired" class="stat-row"><span><i class="dot danger" />审批超时作废</span><b>{{ approval.expired }} 单</b></div>
                </div>
              </div>
            </section>

            <section class="glass-panel">
              <header class="p-head">工单流转</header>
              <div class="fun-rows">
                <div v-for="row in funnelRows" :key="row.label" class="fun-row" :title="`${row.label} ${row.count} 单`">
                  <span class="fun-name">{{ row.label }}</span>
                  <div class="fun-track"><i :class="row.tone" :style="{ width: `${row.pct}%` }" /></div>
                  <b class="fun-count">{{ row.count }}</b>
                </div>
              </div>
              <div v-if="dwellChips.length" class="dwell-grid">
                <div
                  v-for="chip in dwellChips" :key="chip.label" class="dwell-chip"
                  :title="`${chip.label}环节平均滞留 ${chip.hours} 小时（含在途）`"
                >
                  <small>{{ chip.label }}</small><b>{{ chip.hours }}<i>h</i></b>
                </div>
              </div>
            </section>

            <section class="glass-panel">
              <header class="p-head">预案执行力</header>
              <div class="fun-rows">
                <div
                  v-for="row in playbookRows" :key="row.id" class="fun-row pb-row"
                  :title="`${row.name} · 共 ${row.total} 单 · 成功率 ${row.success_rate ?? '—'}%${row.cooldown ? ` · 冷却命中 ${row.cooldown}` : ''}`"
                >
                  <span class="fun-name pb-name">{{ row.name }}</span>
                  <div class="fun-track"><i :class="row.tone" :style="{ width: `${Math.max(row.pct, 6)}%` }" /></div>
                  <b class="fun-count">{{ row.total }}</b>
                </div>
                <div v-if="!playbookRows.length" class="empty-hint">暂无预案执行记录</div>
              </div>
              <div class="stat-rows">
                <div class="stat-row"><span><i class="dot warn" />冷却命中</span><b>{{ playbooks.cooldown_total ?? 0 }} 次</b></div>
                <div class="stat-row"><span>验证一次通过率</span><b>{{ playbooks.verify_first_pass_rate ?? '—' }}<template v-if="playbooks.verify_first_pass_rate != null">%</template></b></div>
              </div>
            </section>
          </aside>

          <!-- 中央：完整让给 3D -->
          <div class="hud-center" aria-hidden="true" />

          <!-- 右栏：告警叙事 -->
          <aside class="hud-col">
            <section class="glass-panel">
              <header class="p-head">告警热点资产 · 近 7 日</header>
              <div class="fun-rows" @mouseleave="highlightKey = ''">
                <div
                  v-for="row in topRows"
                  :key="row.hostname"
                  class="fun-row link"
                  :class="{ active: highlightKey === row.key }"
                  :title="`${row.hostname} · 近 7 日 ${row.count} 条告警`"
                  @mouseenter="highlightKey = row.key"
                >
                  <span class="fun-name">{{ row.name }}</span>
                  <div class="fun-track"><i class="alertbar" :style="{ width: `${row.pct}%` }" /></div>
                  <b class="fun-count">{{ row.count }}</b>
                </div>
                <div v-if="!topRows.length" class="empty-hint">近 7 日风平浪静</div>
              </div>
            </section>

            <section class="glass-panel">
              <header class="p-head">告警热力 · 近 7 日</header>
              <div class="heat-grid">
                <template v-for="(row, di) in heatRows" :key="di">
                  <span class="heat-dow">{{ DOW[di] }}</span>
                  <i
                    v-for="(v, hi) in row"
                    :key="hi"
                    class="heat-cell"
                    :title="`周${DOW[di]} ${String(hi).padStart(2, '0')}:00 · ${v} 条`"
                    :style="{ background: heatColor(v) }"
                  />
                </template>
              </div>
              <div class="heat-axis"><span>00</span><span>06</span><span>12</span><span>18</span><span>23</span></div>
              <div class="heat-foot">
                <span>本周 {{ alerts.total_7d ?? 0 }} 条 · 恢复闭环 {{ alerts.recover_rate ?? 0 }}%</span>
                <span class="heat-legend"><i class="lg lo" />弱<i class="lg hi" />强</span>
              </div>
            </section>

            <section class="glass-panel">
              <header class="p-head">资产健康与容量</header>
              <div class="fun-rows" @mouseleave="highlightKey = ''">
                <div
                  v-for="row in worstRows" :key="row.key" class="fun-row link"
                  :class="{ active: highlightKey === row.key }"
                  :title="`${row.hostname} · 健康分 ${row.score}${row.reasons.length ? ' · ' + row.reasons.join('；') : ''}`"
                  @mouseenter="highlightKey = row.key"
                >
                  <span class="fun-name">{{ row.name }}</span>
                  <div class="fun-track"><i :class="row.tone" :style="{ width: `${row.pct}%` }" /></div>
                  <b class="fun-count" :class="`score-${row.tone}`">{{ row.score }}</b>
                </div>
                <div v-if="!worstRows.length" class="empty-hint">暂无资产数据</div>
              </div>
              <template v-if="capacityRows.length">
                <div class="sub-head">磁盘写满预测（→95%）</div>
                <div class="stat-rows tight">
                  <div v-for="row in capacityRows" :key="row.asset_id" class="stat-row" :title="`${row.hostname} 当前磁盘使用率 ${row.disk_now}%`">
                    <span class="reason">{{ row.name }}</span>
                    <b :class="row.tone === 'danger' ? 'tone-danger' : 'tone-warn'">约 {{ row.days_to_full }} 天</b>
                  </div>
                </div>
              </template>
              <div v-if="capacity.aging_count" class="stat-rows tight">
                <div class="stat-row" :title="`超 180 天未重启：${(capacity.aging_hosts || []).join('、')}`">
                  <span><i class="dot warn" />超 180 天未重启</span><b>{{ capacity.aging_count }} 台</b>
                </div>
              </div>
            </section>

            <section class="glass-panel">
              <header class="p-head">策略灯与红灯归因</header>
              <div class="light-bar">
                <i class="seg ok" :style="{ flexGrow: policy.green || 0.001 }" />
                <i class="seg warn" :style="{ flexGrow: policy.yellow || 0.001 }" />
                <i class="seg danger" :style="{ flexGrow: policy.red || 0.001 }" />
              </div>
              <div class="stat-rows">
                <div class="stat-row"><span><i class="dot ok" />绿灯 · 自动执行</span><b>{{ policy.green ?? 0 }} 单</b></div>
                <div class="stat-row"><span><i class="dot warn" />黄灯 · 需人工审批</span><b>{{ policy.yellow ?? 0 }} 单</b></div>
                <div class="stat-row"><span><i class="dot danger" />红灯 · 升级人工</span><b>{{ policy.red ?? 0 }} 单</b></div>
              </div>
              <template v-if="redReasons.length">
                <div class="sub-head">红灯归因 Top{{ redReasons.length }}</div>
                <div class="stat-rows tight">
                  <div v-for="row in redReasons" :key="row.reason" class="stat-row" :title="row.reason">
                    <span class="reason">{{ row.reason }}</span><b>{{ row.count }}</b>
                  </div>
                </div>
              </template>
              <template v-if="blindSpots.length">
                <div class="sub-head">建议新增预案（知识盲区）</div>
                <div class="stat-rows tight">
                  <div v-for="row in blindSpots" :key="row.trigger" class="stat-row" :title="row.trigger">
                    <span class="reason">{{ row.trigger }}</span><b>{{ row.count }}</b>
                  </div>
                </div>
              </template>
            </section>
          </aside>
        </main>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AssetTopology3D from './AssetTopology3D.vue'
import { fetchDashboardStats, fetchDashboardTopology } from '../api'

// standalone=true 用于独立大屏路由（/screen，脱离应用外壳全屏展示）；
// 默认 false 用于 /dashboard（嵌在应用外壳主内容区内）
defineProps({
  standalone: { type: Boolean, default: false },
})

const topology = ref([])
const stats = ref(null)
const highlightKey = ref('') // 面板行 hover → 3D 节点描边联动

/* ===== 日夜主题：跟随全局 html.dark ===== */
const isDark = ref(document.documentElement.classList.contains('dark'))
let themeObserver
let refreshTimer

function toggleTheme() {
  document.documentElement.classList.add('theme-anim')
  isDark.value = document.documentElement.classList.toggle('dark')
  localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
  setTimeout(() => document.documentElement.classList.remove('theme-anim'), 350)
}

async function load() {
  const [statsResult, topologyResult] = await Promise.allSettled([
    fetchDashboardStats(),
    fetchDashboardTopology(),
  ])
  if (statsResult.status === 'fulfilled') stats.value = statsResult.value.data
  if (topologyResult.status === 'fulfilled') topology.value = topologyResult.value.data.mothers || []
}

onMounted(() => {
  themeObserver = new MutationObserver(() => {
    isDark.value = document.documentElement.classList.contains('dark')
  })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  load()
  refreshTimer = setInterval(load, 30000)
})

onBeforeUnmount(() => {
  themeObserver?.disconnect()
  clearInterval(refreshTimer)
})

/* ===== 派生数据（全部安全兜底，未加载时显示 —） ===== */
const mttr = computed(() => stats.value?.mttr || {})
const automation = computed(() => stats.value?.automation || {})
const alerts = computed(() => stats.value?.alerts || {})
const policy = computed(() => stats.value?.policy || {})
const redReasons = computed(() => policy.value.red_reasons || [])
const mtta = computed(() => stats.value?.mtta || {})
const dwellData = computed(() => stats.value?.dwell || {})
const approval = computed(() => stats.value?.approval || {})
const playbooks = computed(() => stats.value?.playbooks || {})
const aiStats = computed(() => stats.value?.ai || {})
const capacity = computed(() => stats.value?.capacity || {})
const scoreStats = computed(() => stats.value?.scores || {})
const mttrText = computed(() => (mttr.value.recovered_count ? mttr.value.avg_minutes : '—'))
const p50Text = computed(() => (mttr.value.recovered_count ? mttr.value.p50_minutes : '—'))
const rateText = computed(() => (mttr.value.recovered_count ? automation.value.rate : '—'))
const blindSpots = computed(() => aiStats.value.blind_spots || [])

/* 健康分 → 3D 节点着色映射（asset_id 同时映射 m-/c- 前缀，母子节点共用得分） */
const scoreMap = computed(() => {
  const out = {}
  for (const [id, value] of Object.entries(scoreStats.value.map || {})) {
    out[`m-${id}`] = value
    out[`c-${id}`] = value
  }
  return out
})
const worstRows = computed(() =>
  (scoreStats.value.worst || []).map((row) => ({
    ...row,
    key: `${row.kind === 'mother' ? 'm' : 'c'}-${row.asset_id}`,
    name: shortLabel(row.hostname, 12),
    tone: row.score < 40 ? 'danger' : 'warn',
    pct: row.score,
  })),
)
const capacityRows = computed(() =>
  (capacity.value.predictions || []).map((row) => ({
    ...row,
    name: shortLabel(row.hostname, 12),
    tone: row.days_to_full < 14 ? 'danger' : 'warn',
  })),
)
const playbookRows = computed(() =>
  (playbooks.value.items || []).map((row) => ({
    ...row,
    name: shortLabel(row.name, 10),
    pct: row.success_rate ?? 0,
    tone: row.success_rate == null ? 'brand' : row.success_rate >= 80 ? 'ok' : 'danger',
  })),
)
const dwellChips = computed(() =>
  [
    { label: '分析', hours: dwellData.value.analysis },
    { label: '审批', hours: dwellData.value.approval },
    { label: '执行', hours: dwellData.value.executing },
    { label: '验证', hours: dwellData.value.verifying },
  ].filter((chip) => chip.hours != null),
)

/* 自动化闭环率环形（SVG stroke-dashoffset） */
const RING_C = 2 * Math.PI * 36
const ringRate = computed(() => Number(automation.value.rate) || 0)

/* 工单流转漏斗：8 态归并为 7 行，条宽按最大值归一 */
const funnelRows = computed(() => {
  const f = stats.value?.funnel || {}
  const defs = [
    ['分析中', f.pending_analysis, 'brand'],
    ['待审批', f.pending_approval, 'warn'],
    ['待执行', f.pending_execution, 'brand'],
    ['执行中', f.executing, 'brand'],
    ['验证中', f.verifying, 'brand'],
    ['已恢复', f.recovered, 'ok'],
    ['升级人工', f.escalated, 'danger'],
  ]
  const max = Math.max(...defs.map(([, count]) => count || 0), 1)
  return defs.map(([label, count, tone]) => ({
    label,
    count: count || 0,
    tone,
    pct: count ? Math.max((count / max) * 100, 6) : 0,
  }))
})

/* 告警热点：hostname → 3D 节点 key 映射（m-{id}/c-{id}），供联动高亮 */
const hostKeyMap = computed(() => {
  const map = new Map()
  for (const m of topology.value) {
    map.set(m.hostname, `m-${m.id}`)
    for (const c of m.children || []) map.set(c.hostname, `c-${c.id}`)
  }
  return map
})
const topRows = computed(() => {
  const rows = alerts.value.top_assets || []
  const max = rows[0]?.count || 1
  return rows.map((row) => ({
    ...row,
    pct: Math.max((row.count / max) * 100, 8),
    key: hostKeyMap.value.get(row.hostname) || '',
    name: shortLabel(row.hostname, 13),
  }))
})

/* 热力图：7×24（行=周一~周日），颜色强度按全局最大值归一 */
const DOW = ['一', '二', '三', '四', '五', '六', '日']
const heatRows = computed(() => alerts.value.heatmap || Array.from({ length: 7 }, () => Array(24).fill(0)))
function heatColor(value) {
  if (!value) return 'color-mix(in srgb, var(--ink) 6%, transparent)'
  const max = Math.max(1, ...heatRows.value.flat())
  const pct = 14 + (value / max) * 86
  return `color-mix(in srgb, var(--brand) ${Math.round(pct)}%, transparent)`
}

function shortLabel(value, max = 12) {
  const text = String(value || '未知')
  return text.length > max ? `${text.slice(0, max)}…` : text
}
</script>

<style scoped>
/* ===== 舞台：3D 全屏铺底，页面唯一滚动容器 ===== */
.screen-page {
  position: relative;
  height: 100vh;
  overflow: auto;
  background: var(--bg);
}
.screen-page.is-standalone { position: fixed; inset: 0; }
.screen-stage { position: relative; min-height: 100%; min-width: 1180px; }

.topo-backdrop { position: fixed; inset: 0; z-index: 0; }
.topo-backdrop :deep(.topo-scene) { border-radius: 0; }

/* ===== HUD 悬浮层 ===== */
.hud {
  position: relative;
  z-index: 1;
  display: flex;
  min-height: 100vh;
  flex-direction: column;
  padding: 14px 20px 18px;
  pointer-events: none;
}
.screen-page:not(.is-standalone) .hud { padding-top: 60px; }

/* 顶部 KPI 甲板：居中超薄玻璃胶囊 */
.kpi-dock {
  display: flex;
  align-items: center;
  align-self: center;
  gap: 22px;
  margin-bottom: 14px;
  padding: 9px 26px;
  border: 1px solid color-mix(in srgb, var(--ink) 9%, transparent);
  border-radius: var(--r-pill);
  background: color-mix(in srgb, var(--surface) 62%, transparent);
  backdrop-filter: blur(20px) saturate(1.3);
  -webkit-backdrop-filter: blur(20px) saturate(1.3);
  box-shadow: var(--shadow-sm);
  pointer-events: auto;
  animation: dock-in var(--dur-slow) var(--ease-out) both;
}
@keyframes dock-in {
  from { opacity: 0; transform: translateY(-10px); }
  to { opacity: 1; transform: translateY(0); }
}
.dock-brand {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--ink-2);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ok-vivid);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok-vivid) 22%, transparent);
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse { 50% { opacity: 0.45; } }
.dock-sep { width: 1px; height: 26px; background: color-mix(in srgb, var(--ink) 10%, transparent); }
.dock-item { display: flex; flex-direction: column; gap: 1px; white-space: nowrap; }
.dock-item small { color: var(--muted); font-size: 10.5px; letter-spacing: 0.04em; }
.dock-item b { color: var(--ink); font-size: 21px; line-height: 1.1; font-variant-numeric: tabular-nums; }
.dock-item b i { margin-left: 3px; color: var(--faint); font-size: 10px; font-style: normal; font-weight: 400; }

/* standalone 独立路由右上角迷你控件 */
.corner-tools {
  position: absolute;
  top: 16px;
  right: 20px;
  pointer-events: auto;
}
.ghost-btn {
  padding: 6px 14px;
  border: 1px solid color-mix(in srgb, var(--ink) 12%, transparent);
  border-radius: var(--r-pill);
  background: color-mix(in srgb, var(--surface) 62%, transparent);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  color: var(--ink-2);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: border-color var(--dur-fast) ease, color var(--dur-fast) ease;
}
.ghost-btn:hover { border-color: var(--brand); color: var(--brand); }

/* ===== 主体三区：面板环布，中央留白 ===== */
.hud-body { display: flex; flex: 1; gap: 16px; }
.hud-col {
  display: flex;
  width: 318px;
  flex-shrink: 0;
  flex-direction: column;
  gap: 14px;
}
.hud-center { flex: 1; min-width: 0; }

/* ===== 玻璃面板：一层容器，内部去框化（无内嵌卡片） ===== */
.glass-panel {
  padding: 13px 18px 8px;
  border: 1px solid color-mix(in srgb, var(--ink) 8%, transparent);
  border-radius: 18px;
  background: color-mix(in srgb, var(--surface) 58%, transparent);
  backdrop-filter: blur(20px) saturate(1.25);
  -webkit-backdrop-filter: blur(20px) saturate(1.25);
  box-shadow: var(--shadow-sm);
  pointer-events: auto;
  animation: rise-in var(--dur-slow) var(--ease-out) both;
}
.hud-col .glass-panel:nth-child(2) { animation-delay: 0.08s; }
.hud-col .glass-panel:nth-child(3) { animation-delay: 0.16s; }
@keyframes rise-in {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: translateY(0); }
}

.p-head {
  margin-bottom: 4px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
}
.sub-head {
  margin: 8px 0 0;
  color: var(--faint);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
}

/* 数字三件套行：大数字 + 小标签 + 状态点，发丝线分隔 */
.stat-rows { display: flex; flex-direction: column; }
.stat-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 7.5px 0;
  border-bottom: 1px dashed color-mix(in srgb, var(--ink) 8%, transparent);
}
.stat-row:last-child { border-bottom: 0; }
.stat-row span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  overflow: hidden;
  color: var(--ink-2);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stat-row b { color: var(--ink); font-size: 13px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.stat-rows.tight .stat-row { padding: 5.5px 0; }
.stat-rows.tight .stat-row span { color: var(--muted); font-size: 11.5px; }
.reason { overflow: hidden; text-overflow: ellipsis; }
.tone-ok { color: var(--ok) !important; }
.tone-warn { color: var(--warn) !important; }
.tone-danger { color: var(--danger) !important; }
.score-warn { color: var(--warn); }
.score-danger { color: var(--danger); }

/* 环节滞留：2×4 迷你数字块 */
.dwell-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-top: 2px;
  padding: 8px 0 8px;
  border-top: 1px dashed color-mix(in srgb, var(--ink) 8%, transparent);
}
.dwell-chip { display: flex; flex-direction: column; gap: 1px; text-align: center; }
.dwell-chip small { color: var(--faint); font-size: 9.5px; }
.dwell-chip b { color: var(--ink); font-size: 14px; font-variant-numeric: tabular-nums; }
.dwell-chip b i { margin-left: 1px; color: var(--faint); font-size: 9px; font-style: normal; font-weight: 400; }

/* 预案执行力行：名称列稍宽 */
.fun-row.pb-row { grid-template-columns: 82px 1fr 34px; }
.pb-name { text-align: left; }

.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot.ok { background: var(--ok-vivid); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok-vivid) 18%, transparent); }
.dot.warn { background: var(--warn-vivid); box-shadow: 0 0 0 3px color-mix(in srgb, var(--warn-vivid) 18%, transparent); }
.dot.danger { background: var(--danger-vivid); box-shadow: 0 0 0 3px color-mix(in srgb, var(--danger-vivid) 18%, transparent); }

/* 恢复效能 hero 数字 */
.hero { display: flex; flex-direction: column; padding: 6px 0 4px; }
.hero b { color: var(--ink); font-size: 34px; line-height: 1.05; font-variant-numeric: tabular-nums; }
.hero b small { margin-left: 4px; color: var(--faint); font-size: 12px; font-weight: 400; }
.hero span { margin-top: 2px; color: var(--muted); font-size: 11.5px; }

/* 自动化闭环：环形 + 行 */
.auto-wrap { display: flex; align-items: center; gap: 16px; padding: 6px 0; }
.ring-wrap { position: relative; width: 88px; flex-shrink: 0; }
.ring { display: block; width: 88px; height: 88px; transform: rotate(-90deg); }
.ring-track { fill: none; stroke: color-mix(in srgb, var(--ink) 8%, transparent); stroke-width: 7; }
.ring-bar {
  fill: none;
  stroke: var(--brand);
  stroke-linecap: round;
  stroke-width: 7;
  transition: stroke-dashoffset 0.9s var(--ease-out);
}
.ring-center {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
}
.ring-center b { color: var(--ink); font-size: 19px; font-variant-numeric: tabular-nums; }
.ring-center b small { margin-left: 1px; color: var(--faint); font-size: 10px; font-weight: 400; }
.auto-rows { flex: 1; min-width: 0; }

/* 漏斗 / 帕累托：开放行 + 胶囊条 */
.fun-rows { display: flex; flex-direction: column; padding-bottom: 4px; }
.fun-row {
  display: grid;
  grid-template-columns: 62px 1fr 34px;
  align-items: center;
  gap: 10px;
  padding: 6px 6px;
  border-radius: 9px;
  transition: background-color var(--dur-fast) ease;
}
.fun-row.link { cursor: default; }
.fun-row.link:hover, .fun-row.link.active { background: color-mix(in srgb, var(--brand) 8%, transparent); }
.fun-row.link.active .fun-name { color: var(--brand); font-weight: 600; }
.fun-name {
  overflow: hidden;
  color: var(--ink-2);
  font-size: 11.5px;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fun-track {
  position: relative;
  height: 6px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--ink) 7%, transparent);
  overflow: hidden;
}
.fun-track i {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--brand) 55%, transparent), var(--brand));
  transition: width 0.7s var(--ease-out);
}
.fun-track i.warn { background: linear-gradient(90deg, color-mix(in srgb, var(--warn) 55%, transparent), var(--warn)); }
.fun-track i.ok { background: linear-gradient(90deg, color-mix(in srgb, var(--ok) 55%, transparent), var(--ok)); }
.fun-track i.danger { background: linear-gradient(90deg, color-mix(in srgb, var(--danger) 55%, transparent), var(--danger)); }
.fun-track i.alertbar { background: linear-gradient(90deg, color-mix(in srgb, var(--warn) 55%, transparent), var(--warn)); }
.fun-count { color: var(--ink); font-size: 12px; text-align: right; font-variant-numeric: tabular-nums; }
.empty-hint { padding: 12px 0 14px; color: var(--faint); font-size: 11.5px; text-align: center; }

/* 热力图：7×24 低饱和色块矩阵 */
.heat-grid {
  display: grid;
  grid-template-columns: 12px repeat(24, 1fr);
  gap: 3px;
  padding: 6px 0 2px;
}
.heat-dow { color: var(--faint); font-size: 9.5px; line-height: 1; text-align: center; }
.heat-cell { height: 10px; border-radius: 2.5px; transition: background-color var(--dur-fast) ease; }
.heat-axis {
  display: flex;
  justify-content: space-between;
  padding: 3px 0 0 15px;
  color: var(--faint);
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}
.heat-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 0 6px;
  border-top: 1px dashed color-mix(in srgb, var(--ink) 8%, transparent);
  color: var(--muted);
  font-size: 11px;
}
.heat-legend { display: inline-flex; align-items: center; gap: 5px; color: var(--faint); font-size: 10px; }
.heat-legend .lg { display: inline-block; width: 16px; height: 6px; border-radius: 3px; }
.heat-legend .lg.lo { background: color-mix(in srgb, var(--brand) 14%, transparent); }
.heat-legend .lg.hi { background: var(--brand); }

/* 策略灯：三段式胶囊条 */
.light-bar {
  display: flex;
  gap: 3px;
  height: 8px;
  margin: 8px 0 4px;
}
.light-bar .seg { flex-basis: 0; border-radius: 4px; }
.light-bar .seg.ok { background: var(--ok-vivid); }
.light-bar .seg.warn { background: var(--warn-vivid); }
.light-bar .seg.danger { background: var(--danger-vivid); }

@media (prefers-reduced-motion: reduce) {
  .kpi-dock, .glass-panel { animation: none; }
  .live-dot { animation: none; }
}
</style>
