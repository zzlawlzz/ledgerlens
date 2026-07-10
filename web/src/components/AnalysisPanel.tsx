import { useState } from "react";

import type { DictKey } from "../i18n";
import { useI18n } from "../i18n";
import type { RunView } from "../types";

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
      <Plan run={run} />
      <ToolCalls run={run} />
      <Thoughts run={run} />
    </aside>
  );
}

function Plan({ run }: { run: RunView }) {
  const { t } = useI18n();
  if (run.plan.length === 0) return null;
  return (
    <div className="plan" data-testid="plan">
      <h3>{t("plan_title")}</h3>
      <ol>
        {run.plan.map((step) => {
          const live = run.stepStatuses[step.id];
          const status = live?.status ?? step.status;
          const statusKey = STATUS_KEYS[status];
          return (
            <li key={step.id} className={`step step-${status}`} data-testid="plan-step">
              <span className={`status-dot ${status}`} />
              <span className="goal">{step.goal}</span>
              <span className="status-label">{statusKey ? t(statusKey) : status}</span>
              {live?.worker_node && <span className="worker-badge">{live.worker_node}</span>}
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
      {run.toolCalls.map((call) => (
        <details key={call.id} className={call.done ? "tool done" : "tool running"}>
          <summary>
            <code>{call.name}</code> {call.done ? "✓" : "…"}
          </summary>
          <pre className="tool-args">{prettyArgs(call.args)}</pre>
        </details>
      ))}
    </div>
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

function prettyArgs(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
