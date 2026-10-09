<template>
  <BigScreen standalone />
</template>

<script setup>
import BigScreen from '../components/BigScreen.vue'
</script>

<style>
/* 独立大屏（/screen）：页面本身全屏铺满，禁用外层滚动 */
html:has(.screen-page.is-standalone),
body:has(.screen-page.is-standalone) {
  background: #020b1e;
  overflow: hidden;
}
</style>
