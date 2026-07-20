<script setup lang="ts">
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";
import KnowledgeList from "@/components/KnowledgeList.vue";
import RelationGraph from "@/components/RelationGraph.vue";
import { useBookRecallStore } from "@/stores/bookrecall";
import type { DynamicAuditRecord } from "@/types";

const store = useBookRecallStore();
const { state } = storeToRefs(store);

const selectedEntity = ref("");
const editingAuditId = ref("");
const reviewingAuditId = ref("");
const auditDraft = ref({ evidence: "", summary: "", confidence: 0, note: "" });

const auditKindLabels: Record<string, string> = {
  entity_mention: "实体提及",
  relation_mention: "关系提及",
  event: "事件"
};

function auditKindLabel(kind: string) {
  return auditKindLabels[kind] || kind;
}

function confidenceLabel(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, Number(value) || 0)) * 100)}%`;
}

function auditTimeLabel(value?: string) {
  return value ? value.replace("T", " ").slice(0, 19) : "未记录";
}

function auditStatusLabel(status: string) {
  return ({ active: "待审核", confirmed: "已确认", rejected: "已拒绝" } as Record<string, string>)[status] || status;
}

function auditStatusClass(status: string) {
  return `audit-status-${status || "active"}`;
}

function auditRecordTitle(record: DynamicAuditRecord) {
  return record.details?.label || record.details?.entity_name || record.record_id;
}

function beginCorrection(record: DynamicAuditRecord) {
  if (!record.details?.record_exists || !record.details?.mutable) {
    store.setStatus("该记录不能安全地直接修正。");
    return;
  }
  editingAuditId.value = record.audit_id;
  auditDraft.value = {
    evidence: record.details.evidence || record.evidence || "",
    summary: record.details.summary || "",
    confidence: Number(record.confidence || 0),
    note: record.review_note || ""
  };
}

function cancelCorrection() {
  editingAuditId.value = "";
}

async function confirmAudit(record: DynamicAuditRecord) {
  reviewingAuditId.value = record.audit_id;
  try {
    await store.reviewDynamicAudit(record, "confirm", { note: "人工核对通过" });
  } catch (error) {
    store.reportError(error as Error, "动态索引确认失败");
  } finally {
    reviewingAuditId.value = "";
  }
}

async function saveCorrection(record: DynamicAuditRecord) {
  const evidence = auditDraft.value.evidence.trim();
  if (!evidence) {
    store.setStatus("修正后的证据不能为空。");
    return;
  }
  reviewingAuditId.value = record.audit_id;
  try {
    await store.reviewDynamicAudit(record, "correct", {
      evidence,
      summary: auditDraft.value.summary.trim(),
      confidence: Number(auditDraft.value.confidence),
      note: auditDraft.value.note.trim()
    });
    editingAuditId.value = "";
  } catch (error) {
    store.reportError(error as Error, "动态索引修正失败");
  } finally {
    reviewingAuditId.value = "";
  }
}

async function rejectAudit(record: DynamicAuditRecord) {
  const note = prompt("请输入拒绝原因（会保留在审计记录中）：", record.review_note || "");
  if (note === null) {
    return;
  }
  if (!note.trim()) {
    store.setStatus("拒绝动态索引前需要填写原因。");
    return;
  }
  const protectedRecord = record.details?.mutable === false;
  const message = protectedRecord
    ? "这条审计关联已有静态索引。拒绝只会拒绝模型写回，不会删除静态记录，继续吗？"
    : "拒绝后会事务化清理这一条动态索引及无证据的父记录，继续吗？";
  if (!confirm(message)) {
    return;
  }
  reviewingAuditId.value = record.audit_id;
  try {
    await store.reviewDynamicAudit(record, "reject", { note: note.trim() });
    if (editingAuditId.value === record.audit_id) {
      editingAuditId.value = "";
    }
  } catch (error) {
    store.reportError(error as Error, "动态索引拒绝失败");
  } finally {
    reviewingAuditId.value = "";
  }
}

const visibleRelations = computed(() => {
  if (!selectedEntity.value) {
    return state.value.relations.slice(0, 40);
  }
  return state.value.relations.filter((relation) => {
    return relation.source_entity === selectedEntity.value || relation.target_entity === selectedEntity.value;
  });
});

const entityItems = computed(() => {
  return state.value.entities.slice(0, 24).map((entity) => ({
    id: entity.name,
    title: entity.name,
    meta: `首次第 ${entity.first_chapter_number} 章 · ${entity.mention_count} 次`
  }));
});

const eventItems = computed(() => {
  return state.value.events.map((event) => ({
    id: `${event.chapter_number}-${event.summary}`,
    title: `第 ${event.chapter_number} 章 · ${event.event_type}`,
    meta: event.summary,
    detail: event.entities?.length ? `关联实体：${event.entities.join("、")}` : ""
  }));
});

const relationItems = computed(() => {
  return visibleRelations.value.map((relation) => ({
    id: `${relation.source_entity}-${relation.target_entity}-${relation.relation_type}`,
    title: `${relation.source_entity} → ${relation.target_entity}`,
    meta: `${relation.relation_type} · 第 ${relation.first_chapter_number} 章`,
    detail: `${relation.mention_count} 次共现`
  }));
});
</script>

<template>
  <section class="content-page page-rise index-workbench">
    <div class="stats-grid index-stats-grid">
      <article v-for="(value, key) in state.stats" :key="key" class="stat-card">
        <strong>{{ value }}</strong>
        <span>{{ key }}</span>
      </article>
    </div>
    <RelationGraph :relations="state.relations" @focus="(entity) => (selectedEntity = entity)" />
    <details class="control-card dynamic-audit-card">
      <summary class="dynamic-audit-head">
        <div>
          <p class="eyebrow">Dynamic provenance</p>
          <h2>动态索引审计</h2>
          <small>核对问答期由本地模型写回的结构化事实</small>
        </div>
        <div class="dynamic-audit-summary">
          <span class="pill">待审核 {{ state.dynamicAuditStats.pending_total || 0 }}</span>
          <span class="pill audit-pill-confirmed">已确认 {{ state.dynamicAuditStats.confirmed_total || 0 }}</span>
          <span v-if="state.dynamicAuditStats.rejected_total" class="pill audit-pill-rejected">
            已拒绝 {{ state.dynamicAuditStats.rejected_total }}
          </span>
          <span v-if="state.dynamicAuditStats.legacy_untracked_total" class="pill audit-pill-warning">
            历史未追踪 {{ state.dynamicAuditStats.legacy_untracked_total }}
          </span>
          <span class="dynamic-audit-toggle">点击展开</span>
        </div>
      </summary>
      <div class="dynamic-audit-body">
        <div class="dynamic-audit-notice">
          <strong>逐条人工治理</strong>
          <span>确认会保留记录；修正会同步实际索引并退回待审核；拒绝需要原因和二次确认，不会批量处理。</span>
        </div>
        <div class="dynamic-audit-metrics">
          <article v-for="kind in ['entity_mention', 'relation_mention', 'event']" :key="kind">
            <span>{{ auditKindLabel(kind) }}</span>
            <strong>{{ state.dynamicAuditStats.tracked?.[kind] || 0 }}</strong>
            <small>历史未追踪 {{ state.dynamicAuditStats.legacy_untracked?.[kind] || 0 }}</small>
          </article>
        </div>
        <div v-if="state.dynamicAuditRecords.length" class="dynamic-audit-records">
          <article v-for="record in state.dynamicAuditRecords" :key="record.audit_id" class="dynamic-audit-record">
            <header>
              <div>
                <span class="pill">{{ auditKindLabel(record.record_kind) }}</span>
                <span class="pill">第 {{ record.chapter_number }} 章</span>
                <span class="audit-confidence">置信度 {{ confidenceLabel(record.confidence) }}</span>
                <span :class="['audit-status', auditStatusClass(record.status)]">{{ auditStatusLabel(record.status) }}</span>
              </div>
              <button class="text-button" type="button" @click="store.openChapter(record.chapter_number, record.evidence)">
                打开原文
              </button>
            </header>
            <strong class="dynamic-audit-record-title">{{ auditRecordTitle(record) }}</strong>
            <blockquote>{{ record.evidence || "没有保存证据文本。" }}</blockquote>
            <dl>
              <div>
                <dt>来源问题</dt>
                <dd>{{ record.source_query || "未记录" }}</dd>
              </div>
              <div>
                <dt>来源模型</dt>
                <dd>{{ record.source_model || record.source_type || "未记录" }}</dd>
              </div>
              <div>
                <dt>质量门</dt>
                <dd>{{ record.quality_gate || "未记录" }}</dd>
              </div>
              <div>
                <dt>更新时间</dt>
                <dd>{{ auditTimeLabel(record.updated_at || record.created_at) }}</dd>
              </div>
            </dl>
            <p v-if="record.review_note" class="audit-review-note">审核备注：{{ record.review_note }}</p>
            <p v-if="record.details?.mutable === false" class="audit-protected-note">
              关联已有静态索引：可确认或拒绝模型写回，但不会直接改写、删除静态记录。
            </p>
            <form v-if="editingAuditId === record.audit_id" class="audit-correction-form" @submit.prevent="saveCorrection(record)">
              <label>
                原文证据
                <textarea v-model="auditDraft.evidence" rows="4" maxlength="2000"></textarea>
              </label>
              <label v-if="record.record_kind === 'event'">
                事件摘要
                <input v-model="auditDraft.summary" type="text" maxlength="500" />
              </label>
              <div class="audit-correction-row">
                <label>
                  置信度
                  <input v-model.number="auditDraft.confidence" type="number" min="0" max="1" step="0.01" />
                </label>
                <label>
                  修正备注
                  <input v-model="auditDraft.note" type="text" maxlength="500" placeholder="说明为什么修正" />
                </label>
              </div>
              <div class="audit-record-actions">
                <button class="primary-button compact-button" type="submit" :disabled="reviewingAuditId === record.audit_id">
                  保存修正
                </button>
                <button class="secondary-button compact-button" type="button" @click="cancelCorrection">取消</button>
              </div>
            </form>
            <div v-else class="audit-record-actions">
              <button
                v-if="record.status === 'active' && record.details?.record_exists"
                class="primary-button compact-button"
                type="button"
                :disabled="reviewingAuditId === record.audit_id"
                @click="confirmAudit(record)"
              >
                确认
              </button>
              <button
                v-if="record.status !== 'rejected' && record.details?.record_exists && record.details?.mutable"
                class="secondary-button compact-button"
                type="button"
                @click="beginCorrection(record)"
              >
                修正
              </button>
              <button
                v-if="record.status !== 'rejected'"
                class="danger-button compact-button"
                type="button"
                :disabled="reviewingAuditId === record.audit_id"
                @click="rejectAudit(record)"
              >
                拒绝
              </button>
            </div>
          </article>
        </div>
        <div v-else class="empty-state">
          当前没有带审计元数据的新动态记录。问答期成功写回后会显示在这里。
        </div>
      </div>
    </details>
    <div class="knowledge-grid index-knowledge-grid">
      <KnowledgeList title="实体索引" :items="entityItems" empty-text="当前书还没有实体索引。" :default-open="false" compact />
      <KnowledgeList title="事件链" :items="eventItems" empty-text="当前书还没有事件链。" :default-open="false" compact />
      <KnowledgeList title="关系索引" :items="relationItems" empty-text="当前筛选下没有关系。" :default-open="false" compact />
      <section id="reader-panel" class="control-card reader-panel-card">
        <h2>原文阅读器</h2>
        <div class="reader-card chapter-reader">
          <strong>{{ state.reader.title }}</strong>
          <small>{{ state.reader.meta }}</small>
          <article v-html="state.reader.content"></article>
        </div>
      </section>
    </div>
  </section>
</template>
