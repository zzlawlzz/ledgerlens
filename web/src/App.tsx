import { useCallback, useEffect, useState } from "react";

import { EMPTY_RUN, runQuestion } from "./agent";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { Chat } from "./components/Chat";
import { Examples } from "./components/Examples";
import { Header } from "./components/Header";
import { API_BASE } from "./config";
import type { Lang } from "./i18n";
import { detectLang, LangContext } from "./i18n";
import type { RunView } from "./types";

type Theme = "dark" | "light";

function detectTheme(): Theme {
  const saved = localStorage.getItem("theme");
  if (saved === "dark" || saved === "light") return saved;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function App() {
  const [lang, setLangState] = useState<Lang>(detectLang);
  const [run, setRun] = useState<RunView>(EMPTY_RUN);
  const [mode, setMode] = useState<string>("us");
  const [demo, setDemo] = useState<boolean>(false);
  const [lastQuestion, setLastQuestion] = useState<string>("");
  const [theme, setTheme] = useState<Theme>(detectTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    fetch(`${API_BASE}/api/examples`)
      .then((response) => (response.ok ? response.json() : { mode: "us" }))
      .then((body: { mode?: string; demo?: boolean }) => {
        setMode(body.mode ?? "us");
        setDemo(body.demo ?? false);
      })
      .catch(() => setMode("us"));
  }, []);

  const setLang = useCallback((next: Lang) => {
    localStorage.setItem("lang", next);
    setLangState(next);
  }, []);

  const toggleTheme = useCallback(() => {
    const root = document.documentElement;
    root.classList.add("no-anim");
    setTheme((prev) => {
      const next: Theme = prev === "dark" ? "light" : "dark";
      localStorage.setItem("theme", next);
      return next;
    });
    window.setTimeout(() => root.classList.remove("no-anim"), 120);
  }, []);

  const ask = useCallback((question: string) => {
    setLastQuestion(question);
    void runQuestion(question, (mutate) => setRun(mutate));
  }, []);

  const retry = useCallback(() => {
    if (lastQuestion) void runQuestion(lastQuestion, (mutate) => setRun(mutate));
  }, [lastQuestion]);

  const busy = run.phase === "planning" || run.phase === "running";

  return (
    <LangContext.Provider value={{ lang, setLang }}>
      <div className="app">
        <Header run={run} mode={mode} demo={demo} theme={theme} onToggleTheme={toggleTheme} />
        <Examples onPick={ask} disabled={busy} />
        <main className="columns">
          <Chat run={run} question={lastQuestion} onAsk={ask} onRetry={retry} />
          <AnalysisPanel run={run} />
        </main>
      </div>
    </LangContext.Provider>
  );
}
