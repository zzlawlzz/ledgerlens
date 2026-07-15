import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { IconExternal, IconRetry, IconSend, IconSparkle, IconUser } from "../icons";
import { useI18n } from "../i18n";
import { narrate, stepProgress } from "../narration";
import type { RunView } from "../types";

export function Chat({
  run,
  question,
  onAsk,
  onRetry,
}: {
  run: RunView;
  question?: string;
  onAsk: (q: string) => void;
  onRetry?: () => void;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState("");
  const busy = run.phase === "planning" || run.phase === "running";

  const submit = () => {
    const q = draft.trim();
    if (q && !busy) {
      onAsk(q);
      setDraft("");
    }
  };

  const started = run.phase !== "idle";

  return (
    <section className="chat">
      <div className="chat-scroll">
        {!started && (
          <div className="empty" data-testid="empty-state">
            <IconSparkle className="empty-mark" />
            <p>{t("empty_state")}</p>
          </div>
        )}

        {question && started && (
          <div className="msg user">
            <div className="msg-body">{question}</div>
            <span className="avatar avatar-user" aria-hidden="true">
              <IconUser />
            </span>
          </div>
        )}

        {started && (
          <div className="msg assistant">
            <span className="avatar avatar-bot" aria-hidden="true">
              <IconSparkle />
            </span>
            <div className="msg-body">
              {run.phase === "error" ? (
                <ErrorBlock run={run} onRetry={onRetry} />
              ) : run.answer ? (
                <Answer run={run} />
              ) : (
                <Working run={run} />
              )}
            </div>
          </div>
        )}
      </div>

      <div className="ask-row">
        <input
          data-testid="question-input"
          value={draft}
          placeholder={t("ask_placeholder")}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && submit()}
        />
        <button
          data-testid="ask-button"
          className="send"
          onClick={submit}
          disabled={busy}
          aria-label={t("ask_button")}
        >
          <IconSend />
          <span>{t("ask_button")}</span>
        </button>
      </div>
    </section>
  );
}

function Working({ run }: { run: RunView }) {
  const { t } = useI18n();
  const line = narrate(run, t);
  const { done, total, current } = stepProgress(run);
  return (
    <div className="working" data-testid="working">
      <span className="typing" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span className="narrator">{line}</span>
      {total > 0 && (
        <div className="mini-progress" data-testid="mini-progress">
          <div className="mini-bar">
            <span style={{ width: `${(done / total) * 100}%` }} />
          </div>
          <span className="mini-count">
            {t("narrate_step")} {Math.min(current, total)}/{total}
          </span>
        </div>
      )}
    </div>
  );
}

function Answer({ run }: { run: RunView }) {
  const { t } = useI18n();
  return (
    <>
      {run.summary?.partial && <div className="note">{t("partial_note")}</div>}
      {run.guardrail?.triggered && (
        <div className="note" data-testid="guardrail-note">
          {t("guardrail_note")}
        </div>
      )}
      <div className="markdown" data-testid="answer">
        <Markdown remarkPlugins={[remarkGfm]}>{run.answer}</Markdown>
      </div>
      <KeyValuesTable run={run} />
      <CitationCards run={run} />
    </>
  );
}

function ErrorBlock({ run, onRetry }: { run: RunView; onRetry?: () => void }) {
  const { t } = useI18n();
  const message = run.error?.includes("429")
    ? t("rate_limited")
    : run.error === "stalled"
      ? t("stalled")
      : `${t("error_state")} ${run.error}`;
  return (
    <div className="error" data-testid="run-error">
      <p>{message}</p>
      {onRetry && (
        <button type="button" className="retry" onClick={onRetry}>
          <IconRetry />
          <span>{t("retry")}</span>
        </button>
      )}
    </div>
  );
}

function KeyValuesTable({ run }: { run: RunView }) {
  const { t } = useI18n();
  const entries = Object.entries(run.summary?.key_values ?? {});
  if (entries.length === 0) return null;
  return (
    <div className="key-values" data-testid="key-values">
      <h3>{t("key_values_title")}</h3>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>{t("metric")}</th>
              <th>{t("value")}</th>
              <th>{t("period")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([key, kv]) => (
              <tr key={key}>
                <td>{key}</td>
                <td className="num">
                  {kv.value.toLocaleString()} {kv.unit ?? ""}
                </td>
                <td>{kv.period ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CitationCards({ run }: { run: RunView }) {
  const { t } = useI18n();
  const citations = run.summary?.citations ?? [];
  if (citations.length === 0) return null;
  const unique = citations.filter(
    (citation, index) =>
      citations.findIndex(
        (other) => other.source_url === citation.source_url && other.section === citation.section,
      ) === index,
  );
  return (
    <div className="citations" data-testid="citations">
      <h3>{t("citations_title")}</h3>
      <div className="citation-cards">
        {unique.map((citation, index) => (
          <a
            key={index}
            className="citation-card"
            data-testid="citation-card"
            href={citation.source_url ?? "#"}
            target="_blank"
            rel="noreferrer"
            title={t("open_source")}
          >
            <span className="cite-head">
              <strong>
                {citation.ticker} {citation.form_type}
              </strong>
              <IconExternal className="cite-ext" />
            </span>
            <span>{citation.period}</span>
            <span className="section">{citation.section}</span>
          </a>
        ))}
      </div>
    </div>
  );
}
