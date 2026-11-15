<template>
  <section class="page" data-module="apigen">
    <header class="page-head">
      <div>
        <h2>API接口生成管理</h2>
        <p class="page-desc">维护API接口的方法定义、参数配置与响应模板；接口契约只读模型封装的冻结快照，草稿口径调整不影响已发布接口。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出API接口生成清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>接口编号</span>
        <input v-model="filters.keyword" placeholder="按接口编号检索" />
      </label>
      <label class="filter-item">
        <span>接口状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>契约快照</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <template v-if="row['引用封装编号']">
              {{ row['引用封装编号'] }} #{{ row['契约快照号'] }}
              <button class="link" type="button" @click="readContract(row)">查看契约</button>
            </template>
            <span v-else>—</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无API接口生成数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条API接口生成记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="contract" class="drawer-mask" @click.self="contract = null">
      <div class="drawer">
        <div class="drawer-head">
          <div>
            <h3>接口契约 · {{ contract['接口编号'] }}</h3>
            <p class="page-desc">
              引用 {{ contract['引用封装编号'] }} 的冻结快照 #{{ contract['绑定快照号'] }}，只读回放
            </p>
          </div>
          <button class="btn ghost" type="button" @click="contract = null">关闭</button>
        </div>
        <div class="drawer-body">
          <table class="data-table">
            <tbody>
              <tr v-for="(value, key) in contract['契约']['契约快照']" :key="String(key)">
                <th style="width: 140px">{{ key }}</th>
                <td>{{ value }}</td>
              </tr>
            </tbody>
          </table>
          <p class="page-desc" style="margin-top: 12px">
            模型封装草稿当前为 v{{ contract['契约']['草稿版本号'] ?? '—' }}，
            其字段映射后续再调整也不会改变本接口绑定的历史快照口径。
          </p>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

interface ContractSnapshot {
  snapshot_no: number
  封装版本: number
  封装编号: string
  published_at: string
  只读: boolean
  原始数据源: string
  输出结构: string
  字段映射: string
  聚合规则: string
  缓存策略: string
}

interface Contract {
  接口编号: string
  引用封装编号: string
  绑定快照号: number
  只读: boolean
  契约: {
    封装编号: string
    草稿版本号: number | null
    封装状态: string
    契约快照号: number
    契约快照: ContractSnapshot
    只读: boolean
  }
}

const ENDPOINT = '/api/apigen'
const columns = ['接口编号', '接口名称', '请求方法', '请求路径', '参数定义', '响应模板', '超时秒数', '接口状态']
const actions = ['发布接口', '废弃接口', '删除接口']
const statuses = ['草稿', '测试中', '已发布', '已废弃']
const stats = ref([
  { label: '接口配置总数', value: 0 },
  { label: '已发布接口', value: 0 },
  { label: '已废弃接口', value: 0 },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = reactive<Record<string, string>>({ keyword: '', status: '' })
const contract = ref<Contract | null>(null)

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function readContract(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/contract`)
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}))
      throw new Error(payload.detail ?? '接口契约读取失败')
    }
    contract.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接口契约读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? 'API接口生成动作未生效')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'API接口生成操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.keyword) query.set('keyword', filters.keyword)
  if (filters.status) query.set('status', filters.status)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('接口配置列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = [
      { label: '接口配置总数', value: payload.total ?? 0 },
      { label: '已发布接口', value: rows.value.filter((r) => r.status === '已发布').length },
      { label: '已废弃接口', value: rows.value.filter((r) => r.status === '已废弃').length },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'API接口生成列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  justify-content: flex-end;
  z-index: 20;
}
.drawer {
  width: 640px;
  max-width: 92vw;
  height: 100%;
  background: #fff;
  display: flex;
  flex-direction: column;
  box-shadow: -8px 0 24px rgba(15, 23, 42, 0.18);
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.drawer-head h3 { margin: 0 0 4px; }
.drawer-body { padding: 16px 20px; overflow: auto; }
.filter-item select { width: 100%; }
</style>
