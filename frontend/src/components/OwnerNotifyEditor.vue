<template>
  <div class="onf">
    <el-checkbox-group
      :model-value="modelValue.channels"
      :disabled="disabled"
      @update:model-value="set('channels', $event)"
    >
      <el-checkbox v-for="c in CHANNELS" :key="c.value" :value="c.value">{{ c.label }}</el-checkbox>
    </el-checkbox-group>
    <div class="onf-tip">告警发生与 AI 日志分析完成时，向所选渠道推送（至少选一项）</div>
    <div v-if="has('sms')" class="onf-row">
      <span class="onf-label">接收号码</span>
      <el-input
        :model-value="modelValue.phone"
        :disabled="disabled"
        placeholder="负责人手机号（电话/短信）"
        @update:model-value="set('phone', $event)"
      />
    </div>
    <div v-if="has('email')" class="onf-row">
      <span class="onf-label">邮箱地址</span>
      <el-input
        :model-value="modelValue.email"
        :disabled="disabled"
        placeholder="name@example.com"
        @update:model-value="set('email', $event)"
      />
    </div>
    <div v-if="has('dingtalk')" class="onf-row">
      <span class="onf-label">钉钉 Webhook</span>
      <el-input
        :model-value="modelValue.dingtalk_webhook"
        :disabled="disabled"
        placeholder="钉钉自定义机器人 Webhook 地址"
        @update:model-value="set('dingtalk_webhook', $event)"
      />
    </div>
    <div v-if="has('feishu')" class="onf-row">
      <span class="onf-label">飞书 Webhook</span>
      <el-input
        :model-value="modelValue.feishu_webhook"
        :disabled="disabled"
        placeholder="飞书自定义机器人 Webhook 地址"
        @update:model-value="set('feishu_webhook', $event)"
      />
    </div>
    <div v-if="has('wecom')" class="onf-row">
      <span class="onf-label">企微 Webhook</span>
      <el-input
        :model-value="modelValue.wecom_webhook"
        :disabled="disabled"
        placeholder="企业微信群机器人 Webhook 地址"
        @update:model-value="set('wecom_webhook', $event)"
      />
    </div>
  </div>
</template>

<script setup>
// 负责人推送渠道配置（新增子机 / 编辑资产共用）：可多选，所选渠道对应地址必填（提交时校验）。
const props = defineProps({
  modelValue: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const CHANNELS = [
  { value: 'sms', label: '电话短信' },
  { value: 'email', label: '邮箱' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'feishu', label: '飞书' },
  { value: 'wecom', label: '企业微信' },
]

function has(channel) {
  return (props.modelValue.channels || []).includes(channel)
}

function set(key, value) {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}
</script>

<style scoped>
.onf {
  width: 100%;
}
.onf-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 2px 0 8px;
}
.onf-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.onf-label {
  flex: 0 0 96px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  text-align: right;
}
.onf-row .el-input {
  flex: 1;
}
</style>
