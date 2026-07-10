// UI-side mirrors of the stream payloads (CONTRACTS §10; kept minimal).

export interface PlanStep {
  id: string;
  goal: string;
  skill?: string;
  status: string; // pending | running | done | failed | no_data | replanned
  worker_node?: string;
}

export interface ToolCall {
  id: string;
  name: string;
  args: string;
  done: boolean;
}

export interface Citation {
  ticker?: string;
  form_type?: string;
  period?: string;
  section?: string;
  source_url?: string;
  snippet?: string;
}

export interface KeyValue {
  value: number;
  unit?: string;
  period?: string;
}

export interface RunSummary {
  status?: string;
  key_values: Record<string, KeyValue>;
  citations: Citation[];
  partial: boolean;
  usage: { cost_usd?: number; tokens_in?: number; tokens_out?: number };
}

export interface GuardrailInfo {
  triggered: boolean;
  action: string;
}

export type RunPhase = "idle" | "planning" | "running" | "done" | "error";

export interface RunView {
  phase: RunPhase;
  plan: PlanStep[];
  stepStatuses: Record<string, { status: string; worker_node?: string }>;
  toolCalls: ToolCall[];
  thoughts: string[];
  answer: string;
  summary: RunSummary | null;
  guardrail: GuardrailInfo | null;
  replanned: boolean;
  error: string | null;
  llmCalls: number;
}

export interface ExampleQuestion {
  kind: string;
  en: string;
  ru: string;
}
