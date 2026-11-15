<template>
  <section class="page" data-module="datapack">
    <header class="page-head">
      <div>
        <h2>数据模型封装管理</h2>
        <p class="page-desc">维护数据模型封装的字段映射、聚合规则与缓存策略；发布时冻结快照，接口契约只读快照，草稿编辑不影响已发布接口。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出数据模型封装清单</button>
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
        <span>封装编号</span>
        <input v-model="filters.keyword" placeholder="按封装编号检索，如 DATA-0002" />
      </label>
      <label class="filter-item">
        <span>字段映射</span>
        <input v-model="filters.field_mapping" placeholder="按字段映射检索，如 net_gmv" />
      </label>
      <label class="filter-item">
        <span>封装状态</span>
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
          <th>版本</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <button v-if="column === '封装编号'" class="link" type="button" @click="openDetail(row)">
              {{ row[column] ?? '—' }}
            </button>
            <span v-else>{{ row[column] ?? '—' }}</span>
          </td>
          <td>v{{ row.version ?? 1 }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情/编辑</button>
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
          <td :colspan="columns.length + 2" class="empty-state">没有符合封装编号与字段映射条件的模型封装记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据模型封装记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 详情抽屉：上半部分是可编辑草稿工作副本，下半部分是只读发布快照 -->
    <div v-if="detail" class="drawer-mask" @click.self="closeDetail">
      <div class="drawer">
        <div class="drawer-head">
          <div>
            <h3>{{ detail['封装编号'] }} · {{ detail['封装名称'] }}</h3>
            <p class="page-desc">
              草稿版本 v{{ detail['版本号'] }} · 状态 {{ detail.status }}
              <template v-if="detail['最新快照号']"> · 最新冻结快照 #{{ detail['最新快照号'] }}</template>
            </p>
          </div>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </div>

        <div class="drawer-body">
          <section class="detail-block">
            <h4>草稿工作副本（可编辑，保存后版本号 +1）</h4>
            <div v-if="detail.status === '已废弃'" class="readonly-tip">该封装已废弃，内容只读，不能继续编辑。</div>
            <form class="edit-form" @submit.prevent="saveDraft">
              <label v-for="field in editableFields" :key="field" class="filter-item">
                <span>{{ field }}</span>
                <textarea v-if="field === '字段映射' || field === '聚合规则'" v-model="form[field]" rows="2" />
                <input v-else v-model="form[field]" />
              </label>
              <div class="form-foot">
                <button class="btn primary" type="submit" :disabled="detail.status === '已废弃'">
                  保存草稿（基于 v{{ detail['版本号'] }}）
                </button>
                <span v-if="saveHint" :class="saveOk ? 'ok-text' : 'error-text'">{{ saveHint }}</span>
              </div>
            </form>
          </section>

          <section class="detail-block">
            <h4>发布快照（只读，接口契约按此回放）</h4>
            <p v-if="!detail['发布历史']?.length" class="page-desc">尚未发布过，没有冻结快照；发布封装后会在这里生成第一条。</p>
            <table v-else class="data-table snapshot-table">
              <thead>
                <tr>
                  <th>快照号</th>
                  <th>字段映射</th>
                  <th>聚合规则</th>
                  <th>发布时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="snap in detail['发布历史']" :key="String(snap.snapshot_no)">
                  <td>#{{ snap.snapshot_no }}<span v-if="snap.snapshot_no === detail['最新快照号']" class="snap-badge">最新</span></td>
                  <td>{{ snap['字段映射'] }}</td>
                  <td>{{ snap['聚合规则'] }}</td>
                  <td>{{ snap.published_at }}</td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type Detail = Row & { 发布历史?: Row[]; 版本号?: number; 最新快照号?: number }

const ENDPOINT = '/api/datapack'
const columns = ['封装编号', '封装名称', '原始数据源', '输出结构', '字段映射', '聚合规则', '缓存策略', '封装状态']
const editableFields = ['封装名称', '原始数据源', '输出结构', '字段映射', '聚合规则', '缓存策略']
const actions = ['发布封装', '废弃封装', '删除封装']
const statuses = ['草稿', '已封装', '已发布', '已废弃']
const stats = ref([
  { label: '模型封装总数', value: 0 },
  { label: '已发布封装', value: 0 },
  { label: '已废弃封装', value: 0 },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = reactive<Record<string, string>>({ keyword: '', field_mapping: '', status: '' })

const detail = ref<Detail | null>(null)
const form = reactive<Record<string, string>>({})
const saveHint = ref('')
const saveOk = ref(false)

function resetFilters() {
  filters.keyword = ''
  filters.field_mapping = ''
  filters.status = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.keyword) query.set('keyword', filters.keyword)
  if (filters.field_mapping) query.set('字段映射', filters.field_mapping)
  if (filters.status) query.set('status', filters.status)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('模型封装列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = [
      { label: '模型封装总数', value: payload.total ?? 0 },
      { label: '已发布封装', value: rows.value.filter((r) => r.status === '已发布').length },
      { label: '已废弃封装', value: rows.value.filter((r) => r.status === '已废弃').length },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据模型封装列表读取失败'
  }
}

async function openDetail(row: Row) {
  saveHint.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) throw new Error('模型封装详情读取失败')
    detail.value = (await response.json()) as Detail
    editableFields.forEach((field) => {
      form[field] = String(detail.value?.[field] ?? '')
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '模型封装详情读取失败'
  }
}

function closeDetail() {
  detail.value = null
  saveHint.value = ''
}

async function saveDraft() {
  if (!detail.value) return
  saveHint.value = ''
  const values: Record<string, string | number> = { expected_version: detail.value['版本号'] as number }
  editableFields.forEach((field) => {
    values[field] = form[field]
  })
  try {
    const response = await request(`${ENDPOINT}/${detail.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      // 乐观锁冲突（两名编辑同时提交）：提示刷新，不用旧版本覆盖
      saveOk.value = false
      saveHint.value = payload.message ?? '保存失败，请刷新详情后基于最新版本重试'
      return
    }
    saveOk.value = true
    saveHint.value = payload.message
    await reload()
    await openDetail({ id: payload.entry.id } as Row)
  } catch (error) {
    saveOk.value = false
    saveHint.value = error instanceof Error ? error.message : '草稿保存失败'
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
      throw new Error(payload.message ?? '数据模型封装动作未生效')
    }
    if (detail.value && String(detail.value.id) === String(row.id)) {
      if (payload.entry?.deleted) {
        closeDetail()
      } else {
        await openDetail({ id: row.id } as Row)
      }
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据模型封装操作失败'
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
  width: 720px;
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
.detail-block { margin-bottom: 20px; }
.detail-block h4 { margin: 0 0 10px; font-size: 14px; }
.edit-form { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
.edit-form .form-foot { grid-column: 1 / -1; display: flex; gap: 12px; align-items: center; }
.edit-form textarea, .edit-form input { width: 100%; }
.snapshot-table { font-size: 12px; }
.snap-badge {
  margin-left: 6px;
  background: #e8f0fe;
  color: var(--brand);
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 11px;
}
.readonly-tip {
  background: #fef3f2;
  color: #b42318;
  border: 1px solid #fecdca;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  margin-bottom: 10px;
}
.ok-text { color: #067647; }
.filter-item select { width: 100%; }
</style>
