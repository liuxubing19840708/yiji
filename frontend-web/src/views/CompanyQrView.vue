<script setup>
import { onMounted, ref } from 'vue'
import { getSession } from '../api'
import PageState from '../components/PageState.vue'

const loading = ref(true), error = ref(''), qr = ref('')
const session = getSession()
const company = session.companies?.find((item) => item.id === session.companyId)
function download() {
  const link = document.createElement('a'); link.href = qr.value; link.download = `${company?.name || '公司'}-加入二维码.png`; link.click()
}
onMounted(async () => {
  try {
    if (!company?.company_no) throw new Error('未找到当前公司编号')
    const { default: QRCode } = await import('qrcode')
    qr.value = await QRCode.toDataURL(`JOIN:${company.company_no}`, { width: 560, margin: 2, color: { dark: '#000000', light: '#FFFFFF' } })
  } catch (e) { error.value = e.message } finally { loading.value = false }
})
</script>
<template>
  <div class="company-qr-page">
    <PageState :loading="loading" :error="error">
      <section class="company-qr-card"><p>用衣计，扫码加入公司</p><h2>{{ company?.name }}</h2><span>{{ company?.city || '服装计件生产管理' }}</span><img :src="qr" :alt="`${company?.name}加入二维码`" /><strong>{{ company?.company_no }}</strong><small>微信扫码识别 JOIN 编号，或在登录页凭编号加入</small><button class="button company-qr-download" @click="download">保存二维码</button></section>
    </PageState>
  </div>
</template>
