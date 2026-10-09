<template>
  <!-- /dashboard：嵌在应用外壳内，用负边距抵消 .main 的内边距，视觉上铺满可视区 -->
  <div class="dashboard-page">
    <BigScreen />
  </div>
</template>

<script setup>
import BigScreen from '../components/BigScreen.vue'
</script>

<style scoped>
.dashboard-page {
  margin: -54px -22px -100px;
  overflow: auto;
  background: #020b1e;
}

@media (max-width: 1024px) {
  .dashboard-page { margin: -54px -14px -96px; }
}
@media (max-width: 768px) {
  .dashboard-page { margin: -52px -10px -92px; }
}
</style>
