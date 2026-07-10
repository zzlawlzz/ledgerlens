// Tiny locale dictionary (T-024, Q-04). Every static UI string lives here —
// the grep test in tests/contract keeps components literal-free.
import { createContext, useContext } from "react";

export type Lang = "en" | "ru";

const DICT = {
  en: {
    title: "LedgerLens",
    subtitle: "Multi-agent financial analysis",
    disclaimer: "This is financial analytics, not investment advice.",
    ask_placeholder: "Ask about the loaded companies…",
    ask_button: "Ask",
    examples_title: "Try one of these:",
    analysis_title: "Analysis trace",
    plan_title: "Plan",
    thoughts_title: "Agent thoughts",
    tool_calls_title: "Tool calls",
    key_values_title: "Key figures",
    citations_title: "Sources",
    budget_spent: "Run cost",
    empty_state: "Ask a question to see the agents plan, execute and cite sources.",
    waiting_plan: "Planning…",
    running: "Executing…",
    error_state: "Something went wrong. The run failed:",
    rate_limited: "Too many requests — please retry in a minute.",
    partial_note: "The analysis is partial — some steps did not finish.",
    guardrail_note: "The draft was rewritten by the non-advice guardrail.",
    replanned_note: "The plan was revised during the run.",
    step_status_pending: "pending",
    step_status_running: "running",
    step_status_done: "done",
    step_status_failed: "failed",
    step_status_no_data: "no data",
    metric: "Metric",
    value: "Value",
    period: "Period",
    open_source: "Open on sec.gov",
  },
  ru: {
    title: "LedgerLens",
    subtitle: "Мультиагентный финансовый анализ",
    disclaimer: "Это финансовая аналитика, а не инвестиционная рекомендация.",
    ask_placeholder: "Спросите о загруженных компаниях…",
    ask_button: "Спросить",
    examples_title: "Попробуйте один из примеров:",
    analysis_title: "Ход анализа",
    plan_title: "План",
    thoughts_title: "Мысли агента",
    tool_calls_title: "Вызовы инструментов",
    key_values_title: "Ключевые цифры",
    citations_title: "Источники",
    budget_spent: "Стоимость рана",
    empty_state: "Задайте вопрос — увидите план агентов, живые шаги и ответ с цитатами.",
    waiting_plan: "Строим план…",
    running: "Выполняем…",
    error_state: "Что-то пошло не так. Ран завершился ошибкой:",
    rate_limited: "Слишком много запросов — повторите через минуту.",
    partial_note: "Анализ частичный — часть шагов не завершилась.",
    guardrail_note: "Черновик переписан guardrail-фильтром (без рекомендаций).",
    replanned_note: "План был скорректирован по ходу рана.",
    step_status_pending: "ожидает",
    step_status_running: "выполняется",
    step_status_done: "готово",
    step_status_failed: "ошибка",
    step_status_no_data: "нет данных",
    metric: "Метрика",
    value: "Значение",
    period: "Период",
    open_source: "Открыть на sec.gov",
  },
} as const;

export type DictKey = keyof (typeof DICT)["en"];

export function detectLang(): Lang {
  const saved = localStorage.getItem("lang");
  if (saved === "en" || saved === "ru") return saved;
  return navigator.language.toLowerCase().startsWith("ru") ? "ru" : "en";
}

export const LangContext = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({
  lang: "en",
  setLang: () => undefined,
});

export function useI18n(): { lang: Lang; setLang: (l: Lang) => void; t: (k: DictKey) => string } {
  const { lang, setLang } = useContext(LangContext);
  return { lang, setLang, t: (key: DictKey) => DICT[lang][key] };
}
