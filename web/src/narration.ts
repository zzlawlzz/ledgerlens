// Live narrator (T-042): turns the RunView into a friendly "what's happening
// now" line + a step-progress summary. All human strings come from i18n so the
// components stay literal-free; this module only composes keys with numbers.
import type { DictKey } from "./i18n";
import type { RunView } from "./types";

export type ToolKind = "sql" | "rag" | "enrich" | "web" | "generic";

/** Classify a tool by its (backend) name into a display kind. */
export function toolKind(name: string): ToolKind {
  const n = name.toLowerCase();
  if (n.includes("web")) return "web";
  if (n.includes("rag") || n.includes("retriev")) return "rag";
  if (n.includes("sql") || n.includes("schema")) return "sql";
  if (n.includes("price") || n.includes("enrich")) return "enrich";
  return "generic";
}

const TOOL_RUNNING: Record<ToolKind, DictKey> = {
  sql: "tool_running_sql",
  rag: "tool_running_rag",
  enrich: "tool_running_enrich",
  web: "tool_running_web",
  generic: "tool_running_generic",
};

const TOOL_LABEL: Record<ToolKind, DictKey> = {
  sql: "tool_label_sql",
  rag: "tool_label_rag",
  enrich: "tool_label_enrich",
  web: "tool_label_web",
  generic: "tool_label_generic",
};

export function toolLabelKey(name: string): DictKey {
  return TOOL_LABEL[toolKind(name)];
}

const TERMINAL = new Set(["done", "succeeded", "failed", "no_data"]);

/** Steps finished / total, plus the 1-based index of the step in flight. */
export function stepProgress(run: RunView): { done: number; total: number; current: number } {
  const total = run.plan.length;
  let done = 0;
  let running = 0;
  run.plan.forEach((step, index) => {
    const status = run.stepStatuses[step.id]?.status ?? step.status;
    if (TERMINAL.has(status)) done += 1;
    if (status === "running") running = index + 1;
  });
  const current = running || Math.min(done + 1, total);
  return { done, total, current };
}

/** The friendly one-liner shown in the working bubble / narrator strip. */
export function narrate(run: RunView, t: (k: DictKey) => string): string {
  if (run.phase === "planning") return t("narrate_planning");
  if (run.phase === "done") return t("narrate_done");
  if (run.phase !== "running") return "";
  const activeTool = [...run.toolCalls].reverse().find((call) => !call.done);
  if (activeTool) return t(TOOL_RUNNING[toolKind(activeTool.name)]);
  if (run.answer) return t("narrate_synthesizing");
  return t("narrate_running");
}
