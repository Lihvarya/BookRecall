export interface BookSummary {
  book_id: string;
  title: string;
  source_path: string;
  chapter_count: number;
  entity_count: number;
  book_group?: string;
  tags?: string[];
}

export interface RuntimeStatus {
  dependencies?: Record<string, boolean | string>;
  vector_dir?: string;
  model_cache_dir?: string;
  vector_indexes?: VectorIndexSummary[];
  cloud?: {
    providers?: CloudProvider[];
  };
  agent_policies?: RuntimeOption[];
  retrievers?: RuntimeOption[];
}

export interface DiagnosticsStatus {
  ok: boolean;
  database?: {
    path?: string;
    exists?: boolean;
  };
  frontend?: {
    mode?: string;
    dist_index?: string;
    dist_built?: boolean;
    legacy_index?: string;
    legacy_available?: boolean;
  };
  storage?: {
    vector_dir?: string;
    model_cache_dir?: string;
  };
  dependencies?: Record<string, boolean | string>;
  stats?: {
    books?: number;
    chapters?: number;
    entities?: number;
  };
  thread?: string;
}

export interface AgentToolSchema {
  name: string;
  description?: string;
  parameters?: Record<string, Record<string, unknown>>;
  returns?: Record<string, unknown>;
}

export interface AgentToolRun {
  book_id: string;
  user_id: string;
  session_id?: string | null;
  tool_name: string;
  arguments: Record<string, unknown>;
  progress_chapter: number;
  retriever: string;
  result: Record<string, unknown>;
}

export interface RuntimeOption {
  id: string;
  name: string;
  ready: boolean;
}

export interface CloudProvider {
  id: string;
  name: string;
  endpoint: string;
  model: string;
}

export interface VectorIndexSummary {
  book_id: string;
  built: boolean;
  model_name?: string;
  backend?: string;
  chunk_count?: number;
  dimension?: number;
  path?: string;
}

export interface EntitySummary {
  name: string;
  aliases?: string[];
  first_chapter_number: number;
  mention_count: number;
}

export interface ThemeSummary extends EntitySummary {}

export interface ChapterSummary {
  chapter_number: number;
  title: string;
  summary?: string;
}

export interface EventSummary {
  chapter_number: number;
  chapter_title: string;
  event_type: string;
  summary: string;
  excerpt: string;
  entities?: string[];
}

export interface RelationSummary {
  source_entity: string;
  target_entity: string;
  relation_type: string;
  first_chapter_number: number;
  mention_count: number;
}

export interface EvidenceItem {
  chapter_number: number;
  chapter_title?: string;
  excerpt?: string;
  reason?: string;
  child_text?: string;
  score?: number;
}

export interface TraceItem {
  step?: number;
  tool_name?: string;
  arguments?: Record<string, unknown>;
  observation?: unknown;
  observation_summary?: string;
  thought?: string;
  hit_count?: number;
  spoiler_blocked?: boolean;
  blocked_by_spoiler?: boolean;
  elapsed_ms?: number | null;
  status?: string;
}

export interface SessionTurn {
  turn_id: number;
  turn_index: number;
  question: string;
  answer: string;
  summary?: string;
  progress_chapter?: number;
  entity_name?: string;
  matched_entities?: string[];
  trace?: TraceItem[];
}

export interface SessionSummary {
  session_id: string;
  turn_count?: number;
  last_question?: string;
}

export interface SessionComparison {
  summary?: string;
  common_prefix_turns?: number;
  divergence_turn?: number;
  left_session_id?: string;
  right_session_id?: string;
  left_turn_count?: number;
  right_turn_count?: number;
  left_entities?: string[];
  right_entities?: string[];
  shared_entities?: string[];
  entity_delta?: SessionDelta;
  left_tools?: string[];
  right_tools?: string[];
  shared_tools?: string[];
  tool_delta?: SessionDelta;
  left_unique_turns?: SessionTurn[];
  right_unique_turns?: SessionTurn[];
  diff_insights?: SessionDiffInsight[];
  turn_diffs?: SessionTurnDiff[];
}

export interface SessionDelta {
  shared?: string[];
  left_only?: string[];
  right_only?: string[];
}

export interface SessionDiffInsight {
  kind: string;
  title: string;
  detail: string;
}

export interface SessionTurnDiff {
  offset: number;
  status: "left_only" | "right_only" | "same_question" | "different_question" | string;
  left_turn_index?: number | null;
  right_turn_index?: number | null;
  left_question?: string;
  right_question?: string;
  left_answer_excerpt?: string;
  right_answer_excerpt?: string;
  left_summary?: string;
  right_summary?: string;
  left_tools?: string[];
  right_tools?: string[];
}

export interface SessionMerge {
  book_id: string;
  user_id: string;
  left_session_id: string;
  right_session_id: string;
  target_session_id: string;
  common_prefix_turns: number;
  left_unique_turns: number;
  right_unique_turns: number;
  copied_turns: number;
  summary?: string;
  session?: {
    session_id: string;
    turns: SessionTurn[];
  };
}

export interface SessionDigest {
  book_id: string;
  user_id: string;
  session_id: string;
  turn_count: number;
  first_question?: string;
  last_question?: string;
  latest_summary?: string;
  progress_min?: number | null;
  progress_max?: number | null;
  entities?: string[];
  tools?: string[];
  intents?: string[];
  recent_questions?: string[];
  synopsis?: string;
}

export interface AnswerCard {
  intent?: string;
  answer?: string;
  summary?: string;
  progress_chapter?: number;
  entity_name?: string;
  spoiler_blocked?: boolean;
  evidence?: EvidenceItem[];
  suggestions?: string[];
  trace?: TraceItem[];
  user_preferences?: UserPreferences;
  runtime?: Record<string, unknown>;
  session?: {
    session_id?: string;
    turns?: SessionTurn[];
  };
}

export interface UserPreferences {
  answer_style?: string;
  focus?: string;
  custom_prompt?: string;
}

export interface SearchResult {
  retriever?: string;
  effective_retriever?: string;
  hits?: EvidenceItem[];
}
