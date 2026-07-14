// AG-UI client wiring (T-024): one HttpAgent per session (thread = session).
import { HttpAgent } from "@ag-ui/client";

import { API_BASE } from "./config";
import type { Citation, KeyValue, PlanStep, RunSummary, RunView } from "./types";

export const EMPTY_RUN: RunView = {
  phase: "idle",
  plan: [],
  stepStatuses: {},
  toolCalls: [],
  thoughts: [],
  answer: "",
  summary: null,
  guardrail: null,
  replanned: false,
  error: null,
  llmCalls: 0,
};

export function sessionId(): string {
  let sid = localStorage.getItem("session_id");
  if (!sid) {
    sid = crypto.randomUUID();
    localStorage.setItem("session_id", sid);
  }
  return sid;
}

interface StreamEvent {
  type: string;
  snapshot?: { plan?: PlanStep[] };
  delta?: unknown;
  messageId?: string;
  toolCallId?: string;
  toolCallName?: string;
  name?: string;
  value?: unknown;
  message?: string;
}

interface PatchOp {
  op: string;
  path: string;
  value?: unknown;
}

type Update = (mutate: (view: RunView) => RunView) => void;

function applyEvent(view: RunView, event: StreamEvent): RunView {
  switch (event.type) {
    case "RUN_STARTED":
      return { ...view, phase: "planning" };
    case "STATE_SNAPSHOT": {
      const plan = event.snapshot?.plan ?? [];
      return { ...view, phase: "running", plan, stepStatuses: {} };
    }
    case "STATE_DELTA": {
      const next = { ...view, stepStatuses: { ...view.stepStatuses } };
      for (const op of (event.delta as PatchOp[] | undefined) ?? []) {
        if (op.path === "/plan") {
          next.plan = op.value as PlanStep[];
          next.replanned = true;
        } else if (op.path.startsWith("/steps/")) {
          const stepId = op.path.slice("/steps/".length);
          next.stepStatuses[stepId] = op.value as { status: string; worker_node?: string };
        }
      }
      return next;
    }
    case "TOOL_CALL_START":
      return {
        ...view,
        toolCalls: [
          ...view.toolCalls,
          { id: event.toolCallId ?? "", name: event.toolCallName ?? "tool", args: "", done: false },
        ],
      };
    case "TOOL_CALL_ARGS":
      return {
        ...view,
        toolCalls: view.toolCalls.map((call) =>
          call.id === event.toolCallId
            ? { ...call, args: call.args + String(event.delta ?? "") }
            : call,
        ),
      };
    case "TOOL_CALL_END":
      return {
        ...view,
        toolCalls: view.toolCalls.map((call) =>
          call.id === event.toolCallId ? { ...call, done: true } : call,
        ),
      };
    case "TEXT_MESSAGE_CONTENT":
      return { ...view, answer: view.answer + String(event.delta ?? "") };
    case "CUSTOM":
      return applyCustom(view, event);
    case "RUN_FINISHED":
      return { ...view, phase: "done" };
    case "RUN_ERROR":
      return { ...view, phase: "error", error: event.message ?? "unknown" };
    default:
      return view;
  }
}

function applyCustom(view: RunView, event: StreamEvent): RunView {
  const value = event.value as Record<string, unknown> | undefined;
  switch (event.name) {
    case "thought":
      return { ...view, thoughts: [...view.thoughts, String(value?.text ?? "")] };
    case "llm_call":
      return { ...view, llmCalls: view.llmCalls + 1 };
    case "tool_result": {
      const callId = String(value?.tool_call_id ?? "");
      return {
        ...view,
        toolCalls: view.toolCalls.map((call) =>
          call.id === callId
            ? {
                ...call,
                error: value?.status === "error",
                preview: String(value?.preview ?? ""),
              }
            : call,
        ),
      };
    }
    case "guardrail":
      return {
        ...view,
        guardrail: { triggered: Boolean(value?.triggered), action: String(value?.action ?? "") },
      };
    case "run_summary":
      return {
        ...view,
        summary: {
          status: String(value?.status ?? ""),
          key_values: (value?.key_values ?? {}) as Record<string, KeyValue>,
          citations: (value?.citations ?? []) as Citation[],
          partial: Boolean(value?.partial),
          usage: (value?.usage ?? {}) as RunSummary["usage"],
        },
      };
    default:
      return view;
  }
}

// A flaky tunnel can leave the SSE half-open: events stop arriving with no
// error and no EOF, so a run would otherwise hang forever on "planning". The
// AG-UI client ignores the server's keep-alive comments, so we watch the event
// stream itself: if nothing arrives within STALL_MS (reset on every event) the
// stream is treated as dead. Generous enough to sit through one slow LLM call,
// short enough to surface a stuck run for retry instead of a silent freeze.
const STALL_MS = 120000;

export async function runQuestion(question: string, update: Update): Promise<void> {
  const agent = new HttpAgent({ url: `${API_BASE}/agui`, threadId: sessionId() });
  agent.messages = [{ id: crypto.randomUUID(), role: "user", content: question }];
  update(() => ({ ...EMPTY_RUN, phase: "planning" }));

  let stallTimer: ReturnType<typeof setTimeout> | undefined;
  let resetStall = (): void => {};
  const stalled = new Promise<"stalled">((resolve) => {
    resetStall = () => {
      clearTimeout(stallTimer);
      stallTimer = setTimeout(() => resolve("stalled"), STALL_MS);
    };
    resetStall();
  });

  const stream = agent
    .runAgent(
      {},
      {
        onEvent({ event }) {
          resetStall();
          update((view) => applyEvent(view, event as unknown as StreamEvent));
        },
      },
    )
    .then(() => "ok" as const)
    .catch((error) => {
      update((view) =>
        view.phase === "done" ? view : { ...view, phase: "error", error: String(error) },
      );
      return "ok" as const;
    });

  const outcome = await Promise.race([stream, stalled]);
  clearTimeout(stallTimer);
  if (outcome === "stalled") {
    update((view) =>
      view.phase === "done" ? view : { ...view, phase: "error", error: "stalled" },
    );
  }
}
