import { useCallback, useEffect, useState } from "react";

import { EMPTY_RUN, runQuestion } from "./agent";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { Chat } from "./components/Chat";
import { Examples } from "./components/Examples";
import { Header } from "./components/Header";
import type { Lang } from "./i18n";
import { detectLang, LangContext } from "./i18n";
import type { RunView } from "./types";

export function App() {
  const [lang, setLangState] = useState<Lang>(detectLang);
  const [run, setRun] = useState<RunView>(EMPTY_RUN);
  const [mode, setMode] = useState<string>("us");

  useEffect(() => {
    fetch("/api/examples")
      .then((response) => (response.ok ? response.json() : { mode: "us" }))
      .then((body: { mode?: string }) => setMode(body.mode ?? "us"))
      .catch(() => setMode("us"));
  }, []);

  const setLang = useCallback((next: Lang) => {
    localStorage.setItem("lang", next);
    setLangState(next);
  }, []);

  const ask = useCallback((question: string) => {
    void runQuestion(question, (mutate) => setRun(mutate));
  }, []);

  const busy = run.phase === "planning" || run.phase === "running";

  return (
    <LangContext.Provider value={{ lang, setLang }}>
      <div className="app">
        <Header run={run} mode={mode} />
        <Examples onPick={ask} disabled={busy} />
        <main className="columns">
          <Chat run={run} onAsk={ask} />
          <AnalysisPanel run={run} />
        </main>
      </div>
    </LangContext.Provider>
  );
}
