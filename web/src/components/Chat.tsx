import { useState } from "react";

import { useI18n } from "../i18n";
import type { RunView } from "../types";

export function Chat({ run, onAsk }: { run: RunView; onAsk: (q: string) => void }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState("");
  const busy = run.phase === "planning" || run.phase === "running";

  const submit = () => {
    const question = draft.trim();
    if (question && !busy) {
      onAsk(question);
      setDraft("");
    }
  };

  return (
    <section className="chat">
      <div className="chat-scroll">
        {run.phase === "idle" && <p className="empty">{t("empty_state")}</p>}
        {run.phase === "planning" && <p className="status">{t("waiting_plan")}</p>}
        {run.phase === "running" && !run.answer && <p className="status">{t("running")}</p>}
        {run.phase === "error" && (
          <p className="error" data-testid="run-error">
            {run.error?.includes("429") ? t("rate_limited") : `${t("error_state")} ${run.error}`}
          </p>
        )}
        {run.answer && (
          <article className="answer" data-testid="answer">
            {run.summary?.partial && <div className="note">{t("partial_note")}</div>}
            {run.guardrail?.triggered && (
              <div className="note" data-testid="guardrail-note">
                {t("guardrail_note")}
              </div>
            )}
            <pre className="answer-text">{run.answer}</pre>
          </article>
        )}
        <KeyValuesTable run={run} />
        <CitationCards run={run} />
      </div>
      <div className="ask-row">
        <input
          data-testid="question-input"
          value={draft}
          placeholder={t("ask_placeholder")}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && submit()}
        />
        <button data-testid="ask-button" onClick={submit} disabled={busy}>
          {t("ask_button")}
        </button>
      </div>
    </section>
  );
}

function KeyValuesTable({ run }: { run: RunView }) {
  const { t } = useI18n();
  const entries = Object.entries(run.summary?.key_values ?? {});
  if (entries.length === 0) return null;
  return (
    <div className="key-values" data-testid="key-values">
      <h3>{t("key_values_title")}</h3>
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
              <td>
                {kv.value} {kv.unit ?? ""}
              </td>
              <td>{kv.period ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
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
            <strong>
              {citation.ticker} {citation.form_type}
            </strong>
            <span>{citation.period}</span>
            <span className="section">{citation.section}</span>
          </a>
        ))}
      </div>
    </div>
  );
}
