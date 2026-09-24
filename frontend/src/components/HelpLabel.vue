<template>
  <span class="help-label">
    {{ label }}
    <el-tooltip :content="tip" placement="top" :show-after="80" effect="dark">
      <el-icon class="help-icon"><QuestionFilled /></el-icon>
    </el-tooltip>
  </span>
</template>

<script setup>
defineProps({
  label: { type: String, required: true },
  tip: { type: String, required: true },
})
</script>

<style scoped>
.help-label {
  display: inline-flex;
  align-items: center;
}
.help-icon {
  color: #a8abb2;
  font-size: 13px;
  cursor: help;
  margin-left: 2px;
}
</style>
