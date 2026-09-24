<template>
  <div class="login-page">
    <!-- 极简动态背景：仅 transform/opacity 的合成器动画，GPU 加速、CPU 占用极低 -->
    <div class="bg" aria-hidden="true">
      <div class="orb orb-a" />
      <div class="orb orb-b" />
      <div class="orb orb-c" />
      <div class="grid-veil" />
      <div class="vignette" />
    </div>

    <main class="login-panel">
      <!-- 玻璃拟态卡片 -->
      <section class="login-card" :class="{ shake: shaking }" aria-label="登录表单">
        <div class="brand-mark" aria-hidden="true">D</div>
        <h1 class="title">DevOpsAgent</h1>
        <p class="subtitle">智能运维数字员工 · 工作台登录</p>

        <form class="form" novalidate @submit.prevent="doLogin">
          <div class="field" :class="{ invalid: !!errors.username }">
            <label class="field-label" for="login-username">用户名</label>
            <el-input
              id="login-username"
              v-model="form.username"
              placeholder="请输入用户名"
              size="large"
              autocomplete="username"
              autofocus
              :prefix-icon="User"
            />
            <p v-if="errors.username" class="field-error" role="alert">{{ errors.username }}</p>
          </div>

          <div class="field" :class="{ invalid: !!errors.password }">
            <label class="field-label" for="login-password">密码</label>
            <el-input
              id="login-password"
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              size="large"
              show-password
              autocomplete="current-password"
              :prefix-icon="Lock"
            />
            <p v-if="errors.password" class="field-error" role="alert">{{ errors.password }}</p>
          </div>

          <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>

          <el-button
            class="login-btn"
            size="large"
            native-type="submit"
            :loading="loading"
          >
            {{ loading ? '登录中…' : '登 录' }}
          </el-button>
        </form>

        <p class="login-tip">连续失败 5 次将锁定账号 15 分钟</p>
      </section>

      <p class="login-foot">LLM 分析 · 策略决策 · 执行动作 · 证据证明</p>
    </main>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { auth } from '../auth'

const router = useRouter()
const route = useRoute()
const form = reactive({ username: '', password: '' })
const errors = reactive({ username: '', password: '' })
const formError = ref('')
const loading = ref(false)
const shaking = ref(false)
let shakeTimer = null

function validate() {
  errors.username = form.username.trim() ? '' : '请输入用户名'
  errors.password = form.password ? '' : '请输入密码'
  return !errors.username && !errors.password
}

function shake() {
  shaking.value = false
  requestAnimationFrame(() => {
    shaking.value = true
    clearTimeout(shakeTimer)
    shakeTimer = setTimeout(() => (shaking.value = false), 480)
  })
}

async function doLogin() {
  formError.value = ''
  if (!validate()) {
    shake()
    return
  }
  loading.value = true
  try {
    const user = await auth.login(form.username.trim(), form.password)
    ElMessage.success(`欢迎，${user.display_name || user.username}`)
    router.push(route.query.redirect || '/tickets')
  } catch (e) {
    const detail = e.response?.data?.detail
    formError.value = typeof detail === 'string' ? detail : '登录失败，请检查用户名或密码'
    shake()
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
/* ============================================================
   页面容器：深空底色
   ============================================================ */
.login-page {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  display: grid;
  place-items: center;
  overflow: hidden;
  background: #0b0d13;
  font-family: var(--font-stack, -apple-system, BlinkMacSystemFont, sans-serif);
}

/* ============================================================
   动态背景：三颗极光光晕 + 细点网格
   全部使用 transform/opacity（合成器线程动画，不触发重排/重绘）
   ============================================================ */
.bg {
  position: absolute;
  inset: 0;
  pointer-events: none;
  contain: strict;
}
.orb {
  position: absolute;
  border-radius: 50%;
  opacity: 0;
  will-change: transform, opacity;
  transform: translateZ(0);
  animation: orb-in 1.6s ease-out forwards, drift var(--dur) ease-in-out var(--delay) infinite alternate;
}
.orb-a {
  width: 58vmax;
  height: 58vmax;
  left: -20vmax;
  top: -24vmax;
  background: radial-gradient(circle at 32% 32%, rgba(10, 132, 255, 0.5), rgba(10, 132, 255, 0) 62%);
  --dur: 26s;
  --delay: 1.2s;
}
.orb-b {
  width: 50vmax;
  height: 50vmax;
  right: -18vmax;
  bottom: -22vmax;
  background: radial-gradient(circle at 60% 40%, rgba(94, 92, 230, 0.42), rgba(94, 92, 230, 0) 60%);
  --dur: 32s;
  --delay: 1.5s;
}
.orb-c {
  width: 30vmax;
  height: 30vmax;
  left: 56%;
  top: 6%;
  background: radial-gradient(circle at 50% 50%, rgba(100, 210, 255, 0.26), rgba(100, 210, 255, 0) 60%);
  --dur: 22s;
  --delay: 1.8s;
}
@keyframes orb-in {
  to { opacity: 1; }
}
@keyframes drift {
  from { transform: translate3d(0, 0, 0) scale(1); }
  to { transform: translate3d(var(--dx, 5vmax), var(--dy, 4vmax), 0) scale(1.1); }
}
.orb-a { --dx: 6vmax; --dy: 4vmax; }
.orb-b { --dx: -5vmax; --dy: -4vmax; }
.orb-c { --dx: -4vmax; --dy: 5vmax; }

/* 细点网格：静态铺底，营造精密感 */
.grid-veil {
  position: absolute;
  inset: 0;
  background-image: radial-gradient(rgba(255, 255, 255, 0.055) 1px, transparent 1px);
  background-size: 26px 26px;
  -webkit-mask-image: radial-gradient(ellipse 70% 60% at 50% 45%, #000 30%, transparent 75%);
  mask-image: radial-gradient(ellipse 70% 60% at 50% 45%, #000 30%, transparent 75%);
}
/* 暗角：聚焦中央卡片 */
.vignette {
  position: absolute;
  inset: 0;
  background: radial-gradient(ellipse 90% 90% at 50% 45%, transparent 55%, rgba(0, 0, 0, 0.5) 100%);
}

/* ============================================================
   登录卡片：玻璃拟态
   ============================================================ */
.login-panel {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 24px;
  width: 100%;
}
.login-card {
  width: min(400px, 100%);
  padding: 42px 38px 26px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(28px) saturate(160%);
  -webkit-backdrop-filter: blur(28px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.14);
  box-shadow:
    0 24px 80px rgba(0, 0, 0, 0.45),
    inset 0 1px 0 rgba(255, 255, 255, 0.12);
  color: #f5f5f7;
  animation: card-in 0.7s var(--ease-out, cubic-bezier(0.22, 1, 0.36, 1)) both;
}
@keyframes card-in {
  from { opacity: 0; transform: translateY(20px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
/* 校验失败 / 登录失败：轻微水平抖动（目的性反馈） */
.shake { animation: shake 0.42s cubic-bezier(0.36, 0.07, 0.19, 0.97) both; }
@keyframes shake {
  10%, 90% { transform: translateX(-2px); }
  20%, 80% { transform: translateX(3px); }
  30%, 50%, 70% { transform: translateX(-5px); }
  40%, 60% { transform: translateX(5px); }
}

.brand-mark {
  width: 52px;
  height: 52px;
  margin: 0 auto 18px;
  border-radius: 15px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #0a84ff, #5e5ce6);
  color: #fff;
  font-size: 24px;
  font-weight: 700;
  box-shadow: 0 10px 30px rgba(10, 132, 255, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.25);
}
.title {
  margin: 0;
  text-align: center;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #f5f5f7;
}
.subtitle {
  margin: 8px 0 30px;
  text-align: center;
  font-size: 13.5px;
  letter-spacing: 0.02em;
  color: rgba(235, 235, 245, 0.58);
}

/* ---- 表单 ---- */
.form { display: flex; flex-direction: column; }
.field { margin-bottom: 18px; }
.field-label {
  display: block;
  margin-bottom: 7px;
  font-size: 13px;
  font-weight: 500;
  color: rgba(235, 235, 245, 0.62);
}

/* 深色玻璃输入框 */
.form :deep(.el-input__wrapper) {
  background: rgba(255, 255, 255, 0.07);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.14) inset;
  border-radius: 11px;
  padding: 2px 14px;
  transition: box-shadow 0.18s ease, background-color 0.18s ease;
}
.form :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.26) inset;
}
.form :deep(.el-input__wrapper.is-focus) {
  background: rgba(255, 255, 255, 0.1);
  box-shadow:
    0 0 0 1px rgba(255, 255, 255, 0.45) inset,
    0 0 0 3.5px rgba(10, 132, 255, 0.35);
}
.form :deep(.el-input__inner) {
  color: #f5f5f7;
  height: 38px;
  font-size: 15px;
}
.form :deep(.el-input__inner::placeholder) { color: rgba(235, 235, 245, 0.38); }
.form :deep(.el-input__prefix .el-icon) { color: rgba(235, 235, 245, 0.45); }
.form :deep(.el-input__suffix .el-icon) { color: rgba(235, 235, 245, 0.45); }

.field.invalid :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px rgba(255, 69, 58, 0.75) inset, 0 0 0 3px rgba(255, 69, 58, 0.18);
}
.field-error {
  margin: 6px 2px 0;
  font-size: 12px;
  color: #ff8a80;
}
.form-error {
  margin: 0 0 16px;
  padding: 9px 13px;
  border-radius: 10px;
  background: rgba(255, 69, 58, 0.14);
  border: 1px solid rgba(255, 69, 58, 0.3);
  font-size: 13px;
  color: #ffb4ab;
}

/* ---- 登录按钮：Apple 蓝，浮起 + 按压 ---- */
.login-btn {
  width: 100%;
  height: 46px;
  margin-top: 4px;
  border: none;
  border-radius: 12px;
  background: #0a84ff;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.06em;
  box-shadow: 0 8px 24px rgba(10, 132, 255, 0.38);
  transition: transform 0.18s var(--ease-out, ease), box-shadow 0.18s ease, background-color 0.18s ease;
}
.login-btn:hover {
  background: #2492ff;
  transform: translateY(-1px);
  box-shadow: 0 12px 30px rgba(10, 132, 255, 0.45);
}
.login-btn:active { transform: scale(0.975); }

.login-tip {
  margin: 22px 0 0;
  text-align: center;
  font-size: 12px;
  color: rgba(235, 235, 245, 0.42);
}

.login-foot {
  margin: 22px 0 0;
  font-size: 12px;
  letter-spacing: 0.04em;
  color: rgba(235, 235, 245, 0.36);
  text-align: center;
}

/* ============================================================
   响应式
   ============================================================ */
@media (max-width: 480px) {
  .login-card { padding: 34px 24px 22px; border-radius: 20px; }
  .login-panel { padding: 16px; }
  .login-foot { display: none; }
}

/* 减少动态效果：遵循系统设置 */
@media (prefers-reduced-motion: reduce) {
  .orb { animation: none; opacity: 0.9; }
  .login-card, .shake { animation: none; }
}
</style>
