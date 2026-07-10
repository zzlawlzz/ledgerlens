import { useEffect, useState } from "react";

import { useI18n } from "../i18n";
import type { ExampleQuestion } from "../types";

export function Examples({ onPick, disabled }: { onPick: (q: string) => void; disabled: boolean }) {
  const { lang, t } = useI18n();
  const [examples, setExamples] = useState<ExampleQuestion[]>([]);

  useEffect(() => {
    fetch("/api/examples")
      .then((response) => (response.ok ? response.json() : { examples: [] }))
      .then((body: { examples?: ExampleQuestion[] }) =>
        setExamples(Array.isArray(body.examples) ? body.examples : []),
      )
      .catch(() => setExamples([]));
  }, []);

  if (examples.length === 0) return null;
  return (
    <div className="examples" data-testid="examples">
      <span className="examples-title">{t("examples_title")}</span>
      <div className="examples-buttons">
        {examples.map((example) => (
          <button
            key={example.en}
            className="example"
            data-kind={example.kind}
            disabled={disabled}
            onClick={() => onPick(lang === "ru" ? example.ru : example.en)}
          >
            {lang === "ru" ? example.ru : example.en}
          </button>
        ))}
      </div>
    </div>
  );
}
