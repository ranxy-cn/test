<template>
  <div ref="sceneRef" class="topo-scene">
    <div ref="canvasHost" class="topo-canvas" />

    <!-- 悬浮提示：跟随鼠标（同参考 demo 的 Popover 手法） -->
    <div
      v-if="hover.visible"
      class="topo-pop"
      :style="{ left: `${hover.x + 14}px`, top: `${hover.y + 14}px` }"
    >
      <div class="pop-head">
        <i class="dot" :class="hover.node.state === 'ok' ? 'ok' : hover.node.state === 'dbwarn' ? 'db' : 'down'" />
        <b>{{ hover.node.hostname }}</b>
        <span class="pop-kind">{{ hover.node.kind === 'mother' ? '母机' : '子机' }}</span>
      </div>
      <div class="pop-row">IP {{ hover.node.ip || '未登记' }} · {{ hover.node.groupName }}</div>
      <div v-if="hover.node.kind === 'mother'" class="pop-row">子机 {{ hover.node.childrenCount }} 台</div>
      <div v-else class="pop-row">应用 {{ hover.node.app || '—' }}</div>
    </div>

    <!-- 图例（bare 模式下由外层大屏统一展示） -->
    <div v-if="!bare" class="topo-legend">
      <span><i class="dot ok" />在线</span>
      <span><i class="dot down" />不可达</span>
      <span><i class="dot db" />数据库异常</span>
      <span class="legend-hint">拖动旋转 · 滚轮缩放 · 点击节点查看详情</span>
    </div>

    <!-- 选中节点详情（bare 模式由外层大屏展示） -->
    <transition name="pop">
      <div v-if="!bare && selected" class="topo-detail">
        <div class="detail-head">
          <i class="dot" :class="selected.state === 'down' ? 'down' : selected.dbOk ? 'ok' : 'db'" />
          <b>{{ selected.hostname }}</b>
          <span class="detail-kind">{{ selected.kind === 'mother' ? '母机' : '子机' }}</span>
          <button class="detail-close" type="button" @click="selectNode(null)">×</button>
        </div>
        <dl class="detail-grid">
          <div><dt>IP</dt><dd>{{ selected.ip || '未登记' }}</dd></div>
          <div><dt>业务分组</dt><dd>{{ selected.groupName }}</dd></div>
          <div><dt>应用</dt><dd>{{ selected.app || '—' }}</dd></div>
          <div><dt>可达性</dt><dd :class="selected.reachable ? 'status-green' : 'status-red'">{{ selected.reachable ? '正常' : '不可达' }}</dd></div>
          <div><dt>数据库</dt><dd :class="selected.dbOk ? 'status-green' : 'status-yellow'">{{ selected.dbOk ? '正常' : '异常' }}</dd></div>
          <div v-if="selected.kind === 'mother'"><dt>子机</dt><dd>{{ selected.childrenCount }} 台</dd></div>
        </dl>
      </div>
    </transition>

    <!-- 空态 / WebGL 不可用 -->
    <div v-if="!nodes.length || webglFailed" class="topo-empty">
      {{ webglFailed ? '当前浏览器不支持 WebGL2 · 无法渲染 3D 拓扑' : '暂无资产数据 · 请先在资产台账纳管主机' }}
    </div>
  </div>
</template>

<script setup>
// 资产关联 3D 拓扑（WebGL / three.js 版）：
// 母机 = 机房机柜（半透明柜体 + 状态棱线），子机 = 机柜内按业务分组分层堆叠的
// 1U 服务器单元（含状态指示灯），分组间有隔板与侧挂铭牌 —— 关系即「母机(柜) → 分组(层) → 子机(单元)」。
// 手法参考 threejs-demo（Viewer/BoxHelper/Popover 模式），无外部模型、纯代码生成。
// 性能策略：按需渲染（静止不提交 GPU）+ 几何/材质全场景共享缓存 + Phong 替代 PBR
// + 像素比封顶 1.5 + 数据未变跳过重建 + WebGL2 检测降级；相机不自动旋转，仅用户交互时变化。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'

const props = defineProps({
  // 后端 /dashboard/topology 的 mothers 数组（含 children 与孤立节点）
  mothers: { type: Array, default: () => [] },
  // 跟随大屏日夜主题
  dark: { type: Boolean, default: false },
  // bare 模式：图例与详情由外层大屏统一展示
  bare: { type: Boolean, default: false },
  // 外部联动高亮：面板 hover 某资产时传入节点 key（m-{id}/c-{id}），3D 场景描边指示
  highlightKey: { type: String, default: '' },
  // 健康分映射：{ 'm-{id}'/'c-{id}': 0-100 }，< 70 判定为亚健康（strain 黄色态）
  scores: { type: Object, default: () => ({}) },
})

const MAX_MOTHERS = 6 // 展示母机上限
const MAX_CHILDREN = 12 // 每机柜展示子机单元上限
const POD_SPACING = 17 // 机柜间距
const RACK_W = 6.6 // 机柜宽
const RACK_D = 3.2 // 机柜深
const UNIT_H = 0.8 // 服务器单元高（1U 意象）
const UNIT_GAP = 0.18
const GROUP_GAP = 0.42 // 分组层间隔

const sceneRef = ref(null)
const canvasHost = ref(null)
const webglFailed = ref(false)
const selectedKey = ref('')
const hover = ref({ visible: false, x: 0, y: 0, node: null })
const emit = defineEmits(['select'])

/* ===== 主题色板（与 BigScreen palette() 同源） ===== */
function theme() {
  const dark = props.dark
  return {
    bg: dark ? 0x101014 : 0xf5f5f7,
    fogNear: dark ? 55 : 70,
    fogFar: dark ? 150 : 190,
    gridCenter: dark ? 0x2c2c3a : 0xc9c9d4,
    grid: dark ? 0x23232e : 0xd8d8e0,
    ground: dark ? 0x14141a : 0xecedf1,
    platform: dark ? 0x1e1e28 : 0xffffff,
    tower: dark ? 0x2e2e3c : 0xe3e4ea,
    rackGlass: dark ? 0x8fa0c8 : 0x9aa4bb,
    unitBody: dark ? 0x36364a : 0xe8e9ee,
    unitFace: dark ? 0x1b1b26 : 0x3a3b42,
    labelBg: dark ? 'rgba(28,28,36,0.86)' : 'rgba(255,255,255,0.92)',
    labelText: dark ? '#e8e8ed' : '#1d1d1f',
    brand: dark ? 0x0a84ff : 0x0071e3,
    hemiSky: dark ? 0x3a3a55 : 0xffffff,
    hemiGround: dark ? 0x0c0c12 : 0xdcdce4,
    ambient: dark ? 0.5 : 0.75,
  }
}
const STATE_COLORS = {
  ok: { dark: 0x30d158, light: 0x34c759 },
  strain: { dark: 0xffd60a, light: 0xffcc00 }, // 亚健康：健康分 < 70（可达且数据库正常）
  dbwarn: { dark: 0xff9f0a, light: 0xff9500 },
  down: { dark: 0xff453a, light: 0xff3b30 },
}
const stateColor = (state) => (STATE_COLORS[state] || STATE_COLORS.ok)[props.dark ? 'dark' : 'light']

/* ===== 数据铺平：母机 + 子机（截断展示上限） ===== */
const SCORE_WARN = 70
const stateOf = (n) => {
  if (!n.reachable) return 'down'
  if (!n.db_ok) return 'dbwarn'
  const score = props.scores?.[n.key]
  if (score != null && score < SCORE_WARN) return 'strain'
  return 'ok'
}
const nodes = computed(() => {
  const out = []
  for (const m of (props.mothers || []).slice(0, MAX_MOTHERS)) {
    out.push({
      key: `m-${m.id}`, kind: 'mother', hostname: m.hostname, ip: m.ip, app: m.app,
      groupName: m.group || '未分组', reachable: m.reachable, dbOk: m.db_ok,
      state: stateOf(m), childrenCount: (m.children || []).length, raw: m,
    })
    for (const c of (m.children || []).slice(0, MAX_CHILDREN)) {
      out.push({
        key: `c-${c.id}`, kind: 'child', hostname: c.hostname, ip: c.ip, app: c.app,
        groupName: c.group || '未分组', reachable: c.reachable, dbOk: c.db_ok,
        state: stateOf(c), childrenCount: 0, raw: c,
      })
    }
  }
  return out
})
const selected = computed(() => nodes.value.find((n) => n.key === selectedKey.value) || null)

/* ===== three 场景骨架（常驻） ===== */
let renderer, scene, camera, controls, rafAbort = false
let contentGroup = null // 拓扑内容（随数据重建）
let groundGroup = null // 地面 + 网格（随主题重建）
let hoverHelper = null
let focusHelper = null // 外部联动高亮描边（面板 hover → 节点）
let raycaster, pointer
let nodeMeshes = [] // 射线白名单
let needsRender = true // 按需渲染标记：画面无变化时不提交 GPU
let lastTopologyKey = '' // 30s 轮询数据未变时跳过整场重建
let lastScoresKey = '' // 健康分未变时跳过重建

/* ===== 共享几何/材质缓存：全场景复用，大幅降低 DrawCall 状态切换与显存占用 =====
 * 几何按「形状」建一份，材质按「主题×状态」建一份；disposeGroup 跳过共享资源，
 * 由 buildSharedAssets() 在主题切换时统一重建。 */
const sharedGeos = new Set()
const sharedMats = new Set()
const trackGeo = (g) => (sharedGeos.add(g), g)
const trackMat = (m) => (sharedMats.add(m), m)
let GEO = {}
let MAT = {}
let shellCache = new Map() // key: shellH → { geo, edges }（机柜外壳高度随分组数变化）
let backCache = new Map() // key: innerH → geo
let edgesCache = new Map() // key: 基础几何 → EdgesGeometry

function edgesFor(geo) {
  if (!edgesCache.has(geo)) edgesCache.set(geo, trackGeo(new THREE.EdgesGeometry(geo)))
  return edgesCache.get(geo)
}
function shellFor(shellH) {
  if (!shellCache.has(shellH)) {
    const geo = trackGeo(new THREE.BoxGeometry(RACK_W, shellH, RACK_D))
    shellCache.set(shellH, { geo, edges: edgesFor(geo) })
  }
  return shellCache.get(shellH)
}
function backGeoFor(innerH) {
  if (!backCache.has(innerH)) backCache.set(innerH, trackGeo(new THREE.BoxGeometry(RACK_W - 0.5, innerH + 0.5, 0.12)))
  return backCache.get(innerH)
}

function buildSharedAssets() {
  for (const g of sharedGeos) g.dispose()
  for (const m of sharedMats) m.dispose()
  sharedGeos.clear()
  sharedMats.clear()
  shellCache = new Map()
  backCache = new Map()
  edgesCache = new Map()
  const t = theme()
  GEO = {
    unit: trackGeo(new THREE.BoxGeometry(RACK_W - 1.1, UNIT_H, RACK_D - 1.0)),
    face: trackGeo(new THREE.BoxGeometry(RACK_W - 1.3, UNIT_H - 0.16, 0.06)),
    led: trackGeo(new THREE.SphereGeometry(0.075, 10, 10)),
    beacon: trackGeo(new THREE.SphereGeometry(0.15, 12, 12)),
    tower: trackGeo(new THREE.BoxGeometry(2.4, 3.6, 2.4)),
    platform: trackGeo(new THREE.BoxGeometry(RACK_W + 3.4, 0.35, RACK_D + 3.4)),
    divider: trackGeo(new THREE.BoxGeometry(RACK_W - 0.8, 0.05, RACK_D - 0.7)),
    groundPlane: trackGeo(new THREE.PlaneGeometry(220, 220)),
  }
  // MeshPhongMaterial 替代 MeshStandardMaterial：省掉 PBR BRDF 逐像素开销，大画布下更省 GPU
  MAT = {
    platform: trackMat(new THREE.MeshPhongMaterial({ color: t.platform })),
    backPanel: trackMat(new THREE.MeshPhongMaterial({ color: t.tower })),
    shell: trackMat(new THREE.MeshPhongMaterial({
      color: t.rackGlass, transparent: true, opacity: props.dark ? 0.1 : 0.16,
      depthWrite: false, side: THREE.DoubleSide,
    })),
    ground: trackMat(new THREE.MeshPhongMaterial({ color: t.ground, depthWrite: false })),
    face: trackMat(new THREE.MeshPhongMaterial({ color: t.unitFace, shininess: 22, specular: 0x222222 })),
    unit: {},
    tower: {},
    led: {},
    shellEdge: {},
    platEdge: {},
  }
  for (const s of ['ok', 'strain', 'dbwarn', 'down']) {
    const c = stateColor(s)
    MAT.unit[s] = trackMat(new THREE.MeshPhongMaterial({ color: t.unitBody, emissive: c, emissiveIntensity: 0.14, shininess: 18, specular: 0x1c1c1c }))
    MAT.tower[s] = trackMat(new THREE.MeshPhongMaterial({ color: t.tower, emissive: c, emissiveIntensity: 0.08 }))
    MAT.led[s] = trackMat(new THREE.MeshBasicMaterial({ color: c }))
    MAT.shellEdge[s] = trackMat(new THREE.LineBasicMaterial({ color: c, transparent: true, opacity: 0.9 }))
    MAT.platEdge[s] = trackMat(new THREE.LineBasicMaterial({ color: c, transparent: true, opacity: 0.55 }))
  }
}

/* WebGL2 检测：three r186 仅支持 WebGL2，老浏览器（仅 WebGL1）优雅降级为空态 */
function webgl2Available() {
  try {
    const canvas = document.createElement('canvas')
    return !!(window.WebGL2RenderingContext && canvas.getContext('webgl2'))
  } catch {
    return false
  }
}

function initScene() {
  const host = canvasHost.value
  const rawDpr = window.devicePixelRatio || 1
  renderer = new THREE.WebGLRenderer({
    antialias: rawDpr <= 1, // 高分屏像素密度足够，免 MSAA；低分屏才开抗锯齿
    alpha: false,
    powerPreference: 'high-performance',
    failIfMajorPerformanceCaveat: true, // 软渲染等极弱环境直接走空态，避免拖死页面
  })
  renderer.setPixelRatio(Math.min(rawDpr, 1.5)) // 像素比封顶 1.5：4K/Retina 下填充像素至少省一半
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.setSize(host.clientWidth, host.clientHeight)
  host.appendChild(renderer.domElement)

  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(38, host.clientWidth / host.clientHeight, 0.1, 500)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.06
  controls.enablePan = false
  controls.minDistance = 14
  controls.maxDistance = 110
  controls.maxPolarAngle = Math.PI * 0.46
  // 不自动旋转：相机仅在用户拖拽/缩放时变化，配合按需渲染实现静止时零 GPU

  // 灯光：半球光 + 平行光（两盏足够；环境光去掉少一路光照计算，强度并入半球光）
  const t = theme()
  scene.add(new THREE.HemisphereLight(t.hemiSky, t.hemiGround, 1.1 + t.ambient))
  const sun = new THREE.DirectionalLight(0xffffff, 1.6)
  sun.position.set(26, 42, 18)
  scene.add(sun)

  groundGroup = new THREE.Group()
  contentGroup = new THREE.Group()
  scene.add(groundGroup, contentGroup)

  raycaster = new THREE.Raycaster()
  pointer = new THREE.Vector2()

  hoverHelper = new THREE.BoxHelper(new THREE.Object3D(), theme().brand)
  hoverHelper.visible = false
  scene.add(hoverHelper)

  focusHelper = new THREE.BoxHelper(new THREE.Object3D(), theme().brand)
  focusHelper.visible = false
  scene.add(focusHelper)

  applyTheme() // 内含 buildSharedAssets + buildGround + buildContent

  // 交互：拾取白名单 = 节点 mesh；hover 描边 + Popover，click 选中
  const dom = renderer.domElement
  dom.addEventListener('pointermove', onPointerMove)
  dom.addEventListener('pointerleave', onPointerLeave)
  dom.addEventListener('click', onClick)
  dom.style.cursor = 'grab'

  renderer.setAnimationLoop(animate)
}

/* ===== 地面：素面板 + 网格线（同 demo Floors 手法；几何/材质走共享缓存） ===== */
function buildGround() {
  const grid = new THREE.GridHelper(140, 56, theme().gridCenter, theme().grid)
  grid.material.transparent = true
  grid.material.opacity = props.dark ? 0.5 : 0.65
  const plane = new THREE.Mesh(GEO.groundPlane, MAT.ground)
  plane.rotation.x = -Math.PI / 2
  plane.position.y = -0.02
  groundGroup.add(plane, grid)
}

/* ===== 拓扑内容：布局 + 建模 ===== */
function makeLabelSprite(text, { size = 34, bold = true, accent = null } = {}) {
  const t = theme()
  const pad = 18
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')
  const font = `${bold ? '600 ' : ''}${size * 2}px -apple-system, 'PingFang SC', sans-serif`
  ctx.font = font
  const w = Math.ceil(ctx.measureText(text).width) + pad * 2
  const h = size * 2 + 20
  canvas.width = w
  canvas.height = h
  ctx.font = font
  // 圆角底
  const r = h / 2
  ctx.fillStyle = accent || t.labelBg
  ctx.beginPath()
  ctx.moveTo(r, 0); ctx.arcTo(w, 0, w, h, r); ctx.arcTo(w, h, 0, h, r); ctx.arcTo(0, h, 0, 0, r); ctx.arcTo(0, 0, w, 0, r)
  ctx.fill()
  if (accent) { ctx.strokeStyle = 'rgba(255,255,255,0.35)'; ctx.lineWidth = 2; ctx.stroke() }
  ctx.fillStyle = accent ? '#fff' : t.labelText
  ctx.textBaseline = 'middle'
  ctx.fillText(text, pad, h / 2 + 1)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false }))
  // 铭牌渲染尺寸缩小一半
  sprite.scale.set(w / 92, h / 92, 1)
  return sprite
}

function disposeGroup(group) {
  group.traverse((o) => {
    // 共享缓存资源不 dispose（跨重建复用），仅释放一次性资源（如铭牌 CanvasTexture）
    if (o.geometry && !sharedGeos.has(o.geometry)) o.geometry.dispose()
    const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : []
    for (const m of mats) {
      if (sharedMats.has(m)) continue
      m.map?.dispose()
      m.dispose()
    }
  })
  group.clear()
}

function buildContent() {
  disposeGroup(contentGroup)
  nodeMeshes = []

  const mothers = (props.mothers || []).slice(0, MAX_MOTHERS)
  if (!mothers.length) { frameScene(); applyHighlight(); requestRender(); return }

  // 机柜网格布点
  const cols = mothers.length <= 1 ? 1 : mothers.length <= 4 ? mothers.length : Math.ceil(mothers.length / 2)
  const rows = Math.ceil(mothers.length / cols)
  const originX = (-(cols - 1) * POD_SPACING) / 2
  const originZ = (-(rows - 1) * POD_SPACING) / 2

  mothers.forEach((mother, mi) => {
    const px = originX + (mi % cols) * POD_SPACING
    const pz = originZ + Math.floor(mi / cols) * POD_SPACING
    buildPod(mother, px, pz)
  })
  frameScene()
  applyHighlight() // 重建后恢复外部联动描边
  requestRender()
}

function buildPod(mother, px, pz) {
  const mKey = `m-${mother.id}`
  const mState = stateOf(mother)
  const motherNode = nodes.value.find((n) => n.key === mKey)
  const children = (mother.children || []).slice(0, MAX_CHILDREN)

  // —— 基地平台（带状态发光描边） ——
  const platform = new THREE.Mesh(GEO.platform, MAT.platform)
  platform.position.set(px, 0.17, pz)
  const platEdges = new THREE.LineSegments(edgesFor(GEO.platform), MAT.platEdge[mState])
  platEdges.position.copy(platform.position)
  contentGroup.add(platform, platEdges)

  // —— 无子机：退化为母机塔楼（孤立母机意象） ——
  if (!children.length) {
    const tower = new THREE.Mesh(GEO.tower, MAT.tower[mState])
    tower.position.set(px, 1.8 + 0.35, pz)
    tower.userData.topo = motherNode
    const towerEdges = new THREE.LineSegments(edgesFor(GEO.tower), MAT.shellEdge[mState])
    towerEdges.position.copy(tower.position)
    const beacon = new THREE.Mesh(GEO.beacon, MAT.led[mState])
    beacon.position.set(px, 4.1, pz)
    contentGroup.add(tower, towerEdges, beacon)
    nodeMeshes.push(tower)

    const mLabel = makeLabelSprite(short(mother.hostname, 12))
    mLabel.position.set(px, 5.2, pz)
    contentGroup.add(mLabel)
    return
  }

  // —— 有子机：机柜 = 母机；子机按业务分组分层堆叠成 1U 服务器单元 ——
  const clusters = new Map()
  for (const c of children) {
    const g = c.group || '未分组'
    if (!clusters.has(g)) clusters.set(g, [])
    clusters.get(g).push(c)
  }
  const groups = [...clusters.entries()]

  // 计算机柜内高：Σ(单元高度) + 组间距
  const unitPitch = UNIT_H + UNIT_GAP
  let innerH = 0
  groups.forEach(([, members], gi) => {
    innerH += members.length * unitPitch - UNIT_GAP
    if (gi < groups.length - 1) innerH += GROUP_GAP
  })
  const shellH = innerH + 1.1
  const baseY = 0.35 // 平台顶

  // 柜体：半透明玻璃外壳（后画、不写深度，保证内部单元可见）；几何按高度缓存复用
  const shellRes = shellFor(shellH)
  const shell = new THREE.Mesh(shellRes.geo, MAT.shell)
  shell.position.set(px, baseY + shellH / 2, pz)
  shell.renderOrder = 3
  const shellEdges = new THREE.LineSegments(shellRes.edges, MAT.shellEdge[mState])
  shellEdges.position.copy(shell.position)
  // 背板：不透明衬板，突出单元轮廓
  const backPanel = new THREE.Mesh(backGeoFor(innerH), MAT.backPanel)
  backPanel.position.set(px, baseY + (shellH - 0.3) / 2 - 0.15 + 0.15, pz - RACK_D / 2 + 0.28)
  contentGroup.add(shell, shellEdges, backPanel)

  // 逐分组堆叠服务器单元
  let cursorY = baseY + 0.55 + UNIT_H / 2 // 底部留轨位
  groups.forEach(([groupName, members], gi) => {
    const blockTop = cursorY + members.length * unitPitch - UNIT_GAP
    members.forEach((child, ci) => {
      const cKey = `c-${child.id}`
      const state = stateOf(child)
      const y = cursorY + ci * unitPitch

      const unit = new THREE.Mesh(GEO.unit, MAT.unit[state])
      unit.position.set(px, y, pz + 0.12)
      unit.userData.topo = nodes.value.find((n) => n.key === cKey)

      // 前面板：深色面板 + 状态指示灯
      const face = new THREE.Mesh(GEO.face, MAT.face)
      face.position.set(px, y, pz + 0.12 + (RACK_D - 1.0) / 2 + 0.02)
      const led = new THREE.Mesh(GEO.led, MAT.led[state])
      led.position.set(px - (RACK_W - 1.3) / 2 + 0.28, y + UNIT_H / 2 - 0.28, face.position.z + 0.06)
      contentGroup.add(unit, face, led)
      nodeMeshes.push(unit)
    })
    cursorY = blockTop + GROUP_GAP

    // 分组铭牌：挂机柜右侧该层中部
    const label = makeLabelSprite(short(groupName, 8), { size: 24, bold: false })
    label.position.set(px + RACK_W / 2 + 1.5, (blockTop + cursorY - GROUP_GAP) / 2 - UNIT_H / 4, pz)
    contentGroup.add(label)

    // 分组隔板
    if (gi < groups.length - 1) {
      const divider = new THREE.Mesh(GEO.divider, MAT.platform)
      divider.position.set(px, cursorY - GROUP_GAP / 2, pz)
      contentGroup.add(divider)
    }
  })

  // 顶部警示灯 + 母机铭牌
  const beacon = new THREE.Mesh(GEO.beacon, MAT.led[mState])
  beacon.position.set(px, baseY + shellH + 0.35, pz)
  const mLabel = makeLabelSprite(short(mother.hostname, 12))
  mLabel.position.set(px, baseY + shellH + 1.5, pz)
  contentGroup.add(beacon, mLabel)
}

/* 初始机位：按场景包围盒自动拉远（含高度） */
function frameScene() {
  const box = new THREE.Box3().setFromObject(contentGroup.children.length ? contentGroup : new THREE.Object3D())
  const extentXZ = Math.max(box.max.x - box.min.x, box.max.z - box.min.z)
  const extentY = box.max.y - box.min.y
  const extent = Math.max(extentXZ * 1.05, extentY * 1.35, 24)
  camera.position.set(extent * 0.72, extent * 0.62, extent * 0.86)
  controls.target.set(0, Math.max(1.5, extentY * 0.35), 0)
  controls.update()
}

/* ===== 主题切换：背景/雾/网格/灯光/铭牌重建 ===== */
function applyTheme() {
  const t = theme()
  scene.background = new THREE.Color(t.bg)
  scene.fog = new THREE.Fog(t.bg, t.fogNear, t.fogFar)
  const hemi = scene.children.find((o) => o.isHemisphereLight)
  if (hemi) {
    hemi.color.set(t.hemiSky)
    hemi.groundColor.set(t.hemiGround)
    hemi.intensity = 1.1 + t.ambient
  }
  hoverHelper.material.color.set(t.brand)
  focusHelper?.material.color.set(t.brand)
  buildSharedAssets() // 材质随主题色重建
  disposeGroup(groundGroup)
  buildGround()
  buildContent()
}

watch(() => props.dark, () => { if (scene) applyTheme() })
watch(() => props.mothers, () => {
  if (!scene) return
  const key = JSON.stringify(props.mothers)
  if (key === lastTopologyKey) return // 30s 轮询数据未变：跳过整场重建
  lastTopologyKey = key
  buildContent()
}, { deep: true })
watch(() => props.highlightKey, () => { if (scene) applyHighlight() })
watch(() => props.scores, () => {
  if (!scene) return
  const key = JSON.stringify(props.scores)
  if (key === lastScoresKey) return
  lastScoresKey = key
  buildContent() // 健康分变化 → 节点状态色（含 strain）重算
}, { deep: true })

/* ===== 外部联动高亮：面板行 hover 资产 → 对应 3D 节点描边 ===== */
function applyHighlight() {
  const key = props.highlightKey
  const mesh = key ? nodeMeshes.find((m) => m.userData.topo?.key === key) : null
  if (mesh) {
    focusHelper.setFromObject(mesh)
    focusHelper.visible = true
  } else {
    focusHelper.visible = false
  }
  requestRender()
}

/* ===== 交互 ===== */
function pickMesh(event) {
  const rect = renderer.domElement.getBoundingClientRect()
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
  raycaster.setFromCamera(pointer, camera)
  const hits = raycaster.intersectObjects(nodeMeshes, false)
  return hits.length ? hits[0].object : null
}

let lastHit = null
function onPointerMove(event) {
  const rect = sceneRef.value.getBoundingClientRect()
  const hit = pickMesh(event)
  if (hit !== lastHit) {
    lastHit = hit
    if (hit) {
      hoverHelper.setFromObject(hit)
      hoverHelper.visible = true
      renderer.domElement.style.cursor = 'pointer'
    } else {
      hoverHelper.visible = false
      renderer.domElement.style.cursor = 'grab'
    }
    requestRender() // 描边显隐变化才需重绘；纯移动只更新 DOM Popover
  }
  hover.value = {
    visible: !!hit,
    x: event.clientX - rect.left,
    y: event.clientY - rect.top,
    node: hit ? hit.userData.topo : null,
  }
}

function onPointerLeave() {
  if (lastHit) {
    lastHit = null
    hoverHelper.visible = false
    requestRender()
  }
  hover.value = { visible: false, x: 0, y: 0, node: null }
}

function onClick(event) {
  const hit = pickMesh(event)
  selectNode(hit ? hit.userData.topo : null)
}

function selectNode(node) {
  selectedKey.value = node && node.key !== selectedKey.value ? node.key : ''
  emit('select', selected.value)
}

/* ===== 按需渲染循环：画面静止时不提交 GPU（空闲消耗≈0），交互/数据变化才重绘 ===== */
function requestRender() { needsRender = true }
function animate() {
  if (rafAbort) return
  const moved = controls.update() // 阻尼收敛期间返回 true
  if (moved || needsRender) {
    needsRender = false
    renderer.render(scene, camera)
  }
}

/* ===== 尺寸自适应 ===== */
let resizeObserver
function onResize() {
  if (!renderer) return
  const host = canvasHost.value
  if (!host?.clientWidth) return
  camera.aspect = host.clientWidth / host.clientHeight
  camera.updateProjectionMatrix()
  renderer.setSize(host.clientWidth, host.clientHeight)
  requestRender()
}

/* ===== 生命周期 ===== */
function short(value, max) {
  const text = String(value || '')
  return text.length > max ? `${text.slice(0, max)}…` : text
}

onMounted(() => {
  if (!webgl2Available()) {
    webglFailed.value = true // 老浏览器（仅 WebGL1）优雅降级为空态
    return
  }
  try {
    initScene()
    lastTopologyKey = JSON.stringify(props.mothers)
    resizeObserver = new ResizeObserver(onResize)
    resizeObserver.observe(canvasHost.value)
  } catch (err) {
    console.warn('[AssetTopology3D] WebGL 初始化失败', err)
    webglFailed.value = true
  }
})

onBeforeUnmount(() => {
  rafAbort = true
  renderer?.setAnimationLoop(null)
  resizeObserver?.disconnect()
  controls?.dispose()
  scene?.traverse((o) => {
    o.geometry?.dispose()
    if (Array.isArray(o.material)) o.material.forEach((m) => { m.map?.dispose(); m.dispose() })
    else o.material?.dispose?.()
  })
  for (const g of sharedGeos) g.dispose()
  for (const m of sharedMats) m.dispose()
  renderer?.dispose()
  renderer?.domElement?.remove()
})
</script>

<style scoped>
.topo-scene {
  position: relative;
  height: 100%;
  min-height: 380px;
  overflow: hidden;
  border-radius: var(--r-md);
}
.topo-canvas {
  position: absolute;
  inset: 0;
}
.topo-canvas :deep(canvas) {
  display: block;
  width: 100% !important;
  height: 100% !important;
}

/* ===== 悬浮 Popover（鼠标跟随，同参考 demo） ===== */
.topo-pop {
  position: absolute;
  z-index: 5;
  pointer-events: none;
  min-width: 168px;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: color-mix(in srgb, var(--surface) 94%, transparent);
  backdrop-filter: blur(8px);
  box-shadow: var(--shadow-md);
}
.pop-head { display: flex; align-items: center; gap: 6px; }
.pop-head b { max-width: 128px; overflow: hidden; color: var(--ink); font-size: 12.5px; text-overflow: ellipsis; white-space: nowrap; }
.pop-kind {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--el-fill-color-dark);
  color: var(--muted);
  font-size: 10.5px;
}
.pop-row { margin-top: 3px; color: var(--muted); font-size: 11px; }

/* ===== 图例 ===== */
.topo-legend {
  position: absolute;
  top: 10px;
  left: 12px;
  z-index: 3;
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--muted);
  pointer-events: none;
}
.topo-legend .dot { margin-right: 4px; }
.legend-hint { color: var(--faint); font-size: 11px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
.dot.ok { background: var(--ok-vivid); }
.dot.down { background: var(--danger-vivid); }
.dot.db { background: var(--warn-vivid); }

/* ===== 详情浮层 ===== */
.topo-detail {
  position: absolute;
  right: 12px;
  top: 10px;
  z-index: 4;
  width: 248px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: color-mix(in srgb, var(--surface) 96%, transparent);
  backdrop-filter: blur(10px);
  box-shadow: var(--shadow-md);
}
.detail-head { display: flex; align-items: center; gap: 7px; }
.detail-head b { font-size: 13.5px; color: var(--ink); }
.detail-kind {
  padding: 1px 7px;
  border-radius: 6px;
  background: var(--el-fill-color-dark);
  color: var(--muted);
  font-size: 11px;
}
.detail-close {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--faint);
  font-size: 16px;
  cursor: pointer;
}
.detail-close:hover { color: var(--ink); }
.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 7px 10px;
  margin: 10px 0 0;
}
.detail-grid dt { color: var(--faint); font-size: 11px; }
.detail-grid dd { margin: 0; color: var(--ink); font-size: 12.5px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pop-enter-active, .pop-leave-active { transition: opacity var(--dur-fast) ease, transform var(--dur-fast) var(--ease-out); }
.pop-enter-from, .pop-leave-to { opacity: 0; transform: translateY(-6px) scale(0.97); }

/* ===== 空态 ===== */
.topo-empty {
  position: absolute;
  inset: 0;
  z-index: 3;
  display: grid;
  place-items: center;
  color: var(--faint);
  font-size: 13px;
  pointer-events: none;
}
</style>
