<script setup lang="ts">
defineProps<{ failed?: boolean }>()
function reload() { window.location.reload() }
</script>

<template><section class="async-page-notice" role="status">
  <h2>{{ failed ? '对话页面未能加载' : '正在打开对话创作…' }}</h2>
  <p>{{ failed ? '可能刚更新了前端，浏览器仍缓存旧版本。刷新页面后重新加载，会话记录保存在本机。' : '正在加载聊天组件，请稍候。' }}</p>
  <button v-if="failed" type="button" @click="reload">刷新并重新打开</button>
</section></template>

<style scoped>
.async-page-notice{margin:28px;padding:24px;border:1px solid #cbdad2;border-radius:8px;background:#fbfdfb;color:#587069}.async-page-notice h2{margin:0 0 10px;color:#183f3a;font:600 24px/1.3 "KaiTi","STKaiti",serif}.async-page-notice p{font-size:14px;line-height:1.8}.async-page-notice button{border:0;border-radius:6px;padding:10px 16px;background:#326b59;color:#fff;font:inherit}.async-page-notice button:focus-visible{outline:2px solid #79a998;outline-offset:3px}
</style>
