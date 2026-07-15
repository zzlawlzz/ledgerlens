import { useState } from "react";

import { IconBulb, IconCheck, IconSpinner, IconX, StatusIcon, ToolIcon } from "../icons";
import type { DictKey } from "../i18n";
import { useI18n } from "../i18n";
import { toolKind, toolLabelKey } from "../narration";
import type { RunView, ToolCall } from "../types";

const STATUS_KEYS: Record<string, DictKey> = {
  pending: "step_status_pending",
  running: "step_status_running",
  done: "step_status_done",
  succeeded: "step_status_done",
  failed: "step_status_failed",
  no_data: "step_status_no_data",
};

export function AnalysisPanel({ run }: { run: RunView }) {
  const { t } = useI18n();
  if (run.plan.length === 0 && run.toolCalls.length === 0) return null;
  return (
    <aside className="analysis" data-testid="analysis-panel">
      <h2>{t("analysis_title")}</h2>
      {run.replanned && (
        <div className="note" data-testid="replanned-note">
          {t("replanned_note")}
        </div>
      )}
      <Timeline run={run} />
      <ToolCalls run={run} />
      <Thoughts run={run} />
      <Summary run={run} />
    </aside>
  );
}

function Timeline({ run }: { run: RunView }) {
  const { t } = useI18n();
  if (run.plan.length === 0) return null;
  const activeTool = [...run.toolCalls].reverse().find((call) => !call.done);
  return (
    <div className="plan timeline" data-testid="plan">
      <h3>{t("plan_title")}</h3>
      <ol>
        {run.plan.map((step) => {
          const live = run.stepStatuses[step.id];
          const status = live?.status ?? step.status;
          const statusKey = STATUS_KEYS[status];
          const running = status === "running";
          return (
            <li key={step.id} className={`tl-step tl-${status}`} data-testid="plan-step">
              <span className="tl-marker">
                <span className={`status-dot ${status}`}>
                  <StatusIcon status={status} className="dot-icon" />
                </span>
              </span>
              <div className="tl-body">
                <span className="goal">{step.goal}</span>
                <span className="tl-meta">
                  <span className="status-label">{statusKey ? t(statusKey) : status}</span>
                  {live?.worker_node && <span className="worker-badge">{live.worker_node}</span>}
                </span>
                {running && activeTool && (
                  <span className="tl-tool">
                    <ToolIcon kind={toolKind(activeTool.name)} className="tl-tool-icon" />
                    {t(toolLabelKey(activeTool.name))}
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function ToolCalls({ run }: { run: RunView }) {
  const { t } = useI18n();
  if (run.toolCalls.length === 0) return null;
  return (
    <div className="tool-calls" data-testid="tool-calls">
      <h3>{t("tool_calls_title")}</h3>
      <div className="tool-list">
        {run.toolCalls.map((call) => (
          <ToolRow key={call.id} call={call} />
        ))}
      </div>
    </div>
  );
}

function ToolRow({ call }: { call: ToolCall }) {
  const { t } = useI18n();
  const state = call.error ? "errored" : call.done ? "done" : "running";
  return (
    <details
      className={`tool ${state}`}
      data-testid={call.error ? "tool-call-error" : "tool-call"}
    >
      <summary>
        <span className="tool-ic">
          <ToolIcon kind={toolKind(call.name)} />
        </span>
        <code>{call.name}</code>
        <span className="tool-tag">{t(toolLabelKey(call.name))}</span>
        <span className="tool-state" aria-hidden="true">
          {call.error ? <IconX /> : call.done ? <IconCheck /> : <IconSpinner />}
        </span>
      </summary>
      <div className="tool-detail">
        <div className="tool-sub">{t("args_label")}</div>
        <pre className="tool-args">{prettyArgs(call.args)}</pre>
        {call.preview && <pre className="tool-preview">{call.preview}</pre>}
      </div>
    </details>
  );
}

function Thoughts({ run }: { run: RunView }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  if (run.thoughts.length === 0) return null;
  return (
    <div className="thoughts" data-testid="thoughts">
      <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary>
          <IconBulb className="thoughts-ic" />
          {t("thoughts_title")} ({run.thoughts.length})
        </summary>
        <ul>
          {run.thoughts.map((thought, index) => (
            <li key={index}>{thought}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function Summary({ run }: { run: RunView }) {
  const { t } = useI18n();
  const summary = run.summary;
  if (!summary) return null;
  const tokens = (summary.usage.tokens_in ?? 0) + (summary.usage.tokens_out ?? 0);
  const stats: Array<{ key: DictKey; value: string }> = [
    { key: "summary_cost", value: `$${(summary.usage.cost_usd ?? 0).toFixed(4)}` },
    { key: "summary_tokens", value: tokens.toLocaleString() },
    { key: "summary_llm_calls", value: String(run.llmCalls) },
    { key: "summary_tools", value: String(run.toolCalls.length) },
  ];
  return (
    <div className="run-summary" data-testid="run-summary">
      <h3>{t("summary_title")}</h3>
      <div className="summary-grid">
        {stats.map((stat) => (
          <div key={stat.key} className="summary-stat">
            <span className="summary-value">{stat.value}</span>
            <span className="summary-label">{t(stat.key)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function prettyArgs(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
