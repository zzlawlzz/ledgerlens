// Tiny locale dictionary (T-024, Q-04). Every static UI string lives here —
// the grep test in tests/contract keeps components literal-free.
import { createContext, useContext } from "react";

export type Lang = "en" | "ru";

const DICT = {
  en: {
    title: "LedgerLens",
    subtitle: "Multi-agent financial analysis",
    disclaimer: "This is financial analytics, not investment advice.",
    iss_attribution:
      "MOEX ISS data is shown for informational/demo purposes only (delayed Moscow Exchange data).",
    demo_banner: "Public demo — EDGAR filing data, limited budget.",
    demo_repo_link: "Source on GitHub",
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
    empty_state:
      "Ask a question — you'll watch the agents plan, work the steps, and answer with cited sources.",
    waiting_plan: "Planning…",
    running: "Executing…",
    error_state: "Something went wrong. The run failed:",
    rate_limited: "Too many requests — please retry in a minute.",
    stalled: "Connection interrupted (unstable link). Please retry.",
    retry: "Retry",
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
    open_source: "Open source",
    // Live narrator — the friendly "what's happening now" line.
    narrate_planning: "Building the analysis plan…",
    narrate_running: "Analysing…",
    narrate_synthesizing: "Composing the answer…",
    narrate_done: "Done — here's the answer.",
    narrate_step: "Step",
    tool_running_sql: "Querying the database…",
    tool_running_rag: "Searching the filings…",
    tool_running_enrich: "Fetching price data…",
    tool_running_web: "Searching the web…",
    tool_running_generic: "Running a tool…",
    tool_label_sql: "Database",
    tool_label_rag: "Filings",
    tool_label_enrich: "Prices",
    tool_label_web: "Web",
    tool_label_generic: "Tool",
    // Run-summary footer.
    summary_title: "Run summary",
    summary_cost: "Cost",
    summary_tokens: "Tokens",
    summary_llm_calls: "LLM calls",
    summary_tools: "Tool calls",
    // Chat roles + controls.
    role_you: "You",
    thinking_label: "Working",
    theme_toggle: "Toggle light / dark theme",
    args_label: "Arguments",
    steps_label: "steps",
    trust_high: "trusted",
    trust_medium: "cross-checked",
    trust_low: "unverified",
    source_web: "web",
  },
  ru: {
    title: "LedgerLens",
    subtitle: "Мультиагентный финансовый анализ",
    disclaimer: "Это финансовая аналитика, а не инвестиционная рекомендация.",
    iss_attribution:
      "Данные MOEX ISS приведены в ознакомительных/демонстрационных целях (задержанные данные Московской биржи).",
    demo_banner: "Публичное демо — данные EDGAR, бюджет ограничен.",
    demo_repo_link: "Исходный код на GitHub",
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
    empty_state:
      "Задайте вопрос — увидите, как агенты строят план, проходят шаги и отвечают с ссылками на источники.",
    waiting_plan: "Строим план…",
    running: "Выполняем…",
    error_state: "Что-то пошло не так. Ран завершился ошибкой:",
    rate_limited: "Слишком много запросов — повторите через минуту.",
    stalled: "Соединение прервано (нестабильный канал). Повторите.",
    retry: "Повторить",
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
    open_source: "Открыть источник",
    // Живой нарратор — дружелюбная строка «что сейчас происходит».
    narrate_planning: "Строю план анализа…",
    narrate_running: "Анализирую…",
    narrate_synthesizing: "Собираю ответ…",
    narrate_done: "Готово — вот ответ.",
    narrate_step: "Шаг",
    tool_running_sql: "Запрашиваю базу данных…",
    tool_running_rag: "Ищу в отчётности…",
    tool_running_enrich: "Получаю котировки…",
    tool_running_web: "Ищу в интернете…",
    tool_running_generic: "Выполняю инструмент…",
    tool_label_sql: "База",
    tool_label_rag: "Отчётность",
    tool_label_enrich: "Котировки",
    tool_label_web: "Веб",
    tool_label_generic: "Инструмент",
    // Футер-сводка рана.
    summary_title: "Итоги рана",
    summary_cost: "Стоимость",
    summary_tokens: "Токены",
    summary_llm_calls: "Вызовов LLM",
    summary_tools: "Инструментов",
    // Роли чата + управление.
    role_you: "Вы",
    thinking_label: "Работаю",
    theme_toggle: "Переключить светлую / тёмную тему",
    args_label: "Аргументы",
    steps_label: "шагов",
    trust_high: "надёжный",
    trust_medium: "перепроверен",
    trust_low: "не проверен",
    source_web: "веб",
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
