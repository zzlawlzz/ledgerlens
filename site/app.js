"use strict";

/* Two static locales, no server logic. EN is the source; RU mirrors it. */
const I18N = {
  en: {
    nav_how: "How it works",
    nav_arch: "Architecture",
    nav_depth: "Depth",
    nav_bench: "Benchmarks",

    hero_eyebrow: "Self-hostable · multi-agent · eval-gated",
    hero_title: "Financial filings, analyzed like an analyst — not searched like a box.",
    hero_lede:
      "Ask in plain language. LedgerLens builds an explicit multi-step plan, its agents work over structured facts (SQL) and narrative disclosures (RAG), reach the web with trust-tiered search when the corpus can't answer, self-correct when a step comes back empty — and every claim carries a citation to its source.",
    cta_demo: "Try the live demo",
    cta_github: "View on GitHub",
    cta_contact: "Contact",
    hero_demo_note:
      "The demo is public, rate- and budget-limited, and runs on a workstation backend exposed through a small VPS — it may be offline during maintenance.",

    how_title: "Watch it self-correct",
    how_lede:
      "The signature scenario: a step returns nothing, the orchestrator re-plans it in view, and the retry produces a cited answer. Reasoning streams first (AG-UI), so you see the analyst think.",
    how_cap:
      "Left: an empty step is re-planned live and the data boundary is conceded honestly. Right: a narrative answer with per-claim sec.gov citations.",
    how_pending: "▶ A 60–90 s screen capture of this flow is in the pipeline.",

    arch_title: "Architecture",
    arch_lede:
      "A Plan-and-Execute orchestrator delegates to ReAct workers over A2A — one local, a second node pluggable via the same contract. Tools are MCP servers — SQL, RAG, price and a trust-tiered web search that enriches the DB; the browser is fed by an AG-UI event stream. Hover a layer.",
    tip_default: "Hover a component for detail.",
    tip_ui: "Web UI — React/TypeScript, rendering the AG-UI event stream (plan, steps, tool calls, citations) live.",
    tip_orch: "Orchestrator — LangGraph Plan-and-Execute: plans, dispatches steps, assesses results and re-plans on failure.",
    tip_w1: "Local ReAct worker — runs tools in a reason-act loop; same A2A contract as any added node.",
    tip_w2: "Optional second ReAct worker node — same A2A contract; round-robin with local-preferred failover.",
    tip_router: "Model Router — tiered: local CPU for classify/extract/guard, cloud API for plan/synthesize, with cost tracking.",
    tip_tools: "MCP tool servers — sql_query, rag_search (hybrid + rerank), price_enrich; swappable and contract-tested.",
    tip_web: "web_search — reached only when SQL/RAG can't answer. Scores each source by a domain-trust tier, cross-checks when none is trusted, and caches the facts it finds into web_facts so repeats hit the DB, not the network.",
    tip_pg: "Postgres — normalized filing facts, web-sourced web_facts and pgvector; the read-only role backs Grafana.",
    tip_qd: "Qdrant — narrative disclosure vectors for hybrid retrieval with citations.",

    depth_title: "Depth markers",
    depth_lede: "The parts that make this a system, not a demo — each with a proof you can open.",
    depth_eval_t: "Eval-in-CI",
    depth_eval_d: "A 41-case golden set gated in GitHub Actions with quality thresholds (faithfulness, citation coverage, guardrail, numeric accuracy).",
    depth_eval_p: "eval.yml runs →",
    depth_a2a_t: "Pluggable A2A nodes",
    depth_a2a_d: "Workers speak the same A2A contract, so a second node is one config entry — round-robin with local-preferred failover. The public demo runs a single local worker.",
    depth_a2a_p: "T-031 · config/workers.yaml",
    depth_mcp_t: "MCP tools",
    depth_mcp_d: "sql_query, rag_search and price_enrich run as MCP servers — swappable, contract-tested, callable by any agent.",
    depth_mcp_p: "T-027 · MCP servers + clients",
    depth_route_t: "Tiered LLM routing",
    depth_route_d: "Cheap/local CPU for classify/extract/guard, cloud API for plan/synthesize — provider-agnostic behind one interface, with cost tracking.",
    depth_route_p: "inference benchmark →",
    depth_guard_t: "Groundedness guardrail",
    depth_guard_d: "A non-advice guardrail blocks recommendation-shaped output, and a groundedness pass strips synthesis that isn't backed by retrieved context.",
    depth_guard_p: "T-022 · T-041",
    depth_obs_t: "Observability",
    depth_obs_d: "Every run's steps, tokens, cost, latency and local-vs-cloud split land in Postgres and surface in Grafana (read-only role).",
    depth_obs_p: "Grafana quality board →",

    bench_title: "Benchmarks, measured",
    bench_lede: "Routing and storage choices are backed by live numbers, not vibes.",
    bench_inf_t: "Inference — routing rationale",
    bench_col_model: "Model",
    bench_col_cost: "Cost /1k",
    bench_col_quality: "Judge",
    bench_inf_take:
      "Flash is ~10× cheaper and ~4× faster to first token — so it handles routing/extraction/guarding; the pro tier is reserved for planning and synthesis.",
    bench_vec_t: "Vector store — pgvector vs Qdrant",
    bench_col_store: "Store",
    bench_col_lat: "Latency p50",
    bench_vec_take:
      "Equal recall; Qdrant is markedly faster at query time and adds native hybrid (dense + BM25) — which is why narrative retrieval lives there while facts stay in Postgres.",

    close_title: "Run it yourself",
    close_lede:
      "Clone, set a few env vars, and make demo brings up the full stack with data in minutes — no EDGAR round-trip needed.",

    footer_disclaimer:
      "LedgerLens analyses public filings and market data. It is not an investment adviser and gives no buy/sell/hold recommendations, price targets or personalised advice. Verify figures against the cited primary source before relying on them.",
    footer_licensing:
      "Data: SEC EDGAR (fair-use, with User-Agent & rate limits) · MOEX ISS (informational / demonstration use only). See the repository for full licensing.",
    footer_demo: "Live demo",
  },

  ru: {
    nav_how: "Как работает",
    nav_arch: "Архитектура",
    nav_depth: "Глубина",
    nav_bench: "Бенчмарки",

    hero_eyebrow: "Self-hosted · мультиагентная · с eval-гейтом",
    hero_title: "Отчётность компаний — анализ как у аналитика, а не поиск по коробке.",
    hero_lede:
      "Спросите обычным языком. LedgerLens строит явный многошаговый план, агенты работают с числовыми фактами (SQL) и нарративными разделами (RAG), идут в веб с поиском по тирам доверия, когда корпус не отвечает, сами переигрывают пустой шаг — и каждое утверждение снабжено ссылкой на источник.",
    cta_demo: "Открыть живое демо",
    cta_github: "Смотреть на GitHub",
    cta_contact: "Связаться",
    hero_demo_note:
      "Демо публичное, с лимитами по частоте и бюджету, работает на воркстейшн-бэкенде через малый VPS — может быть недоступно во время обслуживания.",

    how_title: "Как система переигрывает шаг",
    how_lede:
      "Ключевой сценарий: шаг вернул пусто, оркестратор переигрывает его на глазах, и повтор даёт ответ с цитатами. Рассуждение стримится первым (AG-UI) — видно, как «думает» аналитик.",
    how_cap:
      "Слева: пустой шаг переигрывается вживую, а граница данных признаётся честно. Справа: нарративный ответ с цитатами sec.gov по каждому утверждению.",
    how_pending: "▶ Экранная запись этого сценария на 60–90 с — в работе.",

    arch_title: "Архитектура",
    arch_lede:
      "Оркестратор Plan-and-Execute делегирует ReAct-воркерам по A2A — один локальный, вторая нода подключается тем же контрактом. Инструменты — MCP-серверы: SQL, RAG, цены и веб-поиск с тирами доверия, обогащающий БД; в браузер идёт поток событий AG-UI. Наведите на слой.",
    tip_default: "Наведите на компонент для деталей.",
    tip_ui: "Web UI — React/TypeScript, рендерит поток событий AG-UI (план, шаги, вызовы инструментов, цитаты) вживую.",
    tip_orch: "Оркестратор — LangGraph Plan-and-Execute: планирует, раздаёт шаги, оценивает результаты и переигрывает при сбое.",
    tip_w1: "Локальный ReAct-воркер — гоняет инструменты в цикле reason-act; тот же A2A-контракт, что и у любой добавленной ноды.",
    tip_w2: "Опциональная вторая ReAct-нода — тот же A2A-контракт; round-robin с приоритетом локальной ноды при failover.",
    tip_router: "Model Router — многоуровневый: локальный CPU для классификации/извлечения/guard, облачный API для плана/синтеза, с учётом стоимости.",
    tip_tools: "MCP-инструменты — sql_query, rag_search (hybrid + rerank), price_enrich; заменяемы и покрыты контракт-тестами.",
    tip_web: "web_search — вызывается, только когда SQL/RAG не отвечают. Оценивает каждый источник тиром доверия, кросс-проверяет при отсутствии доверенного и кэширует найденные факты в web_facts, чтобы повторы били в БД, а не в сеть.",
    tip_pg: "Postgres — нормализованные факты отчётности, веб-факты web_facts и pgvector; read-only роль питает Grafana.",
    tip_qd: "Qdrant — векторы нарративных разделов для гибридного поиска с цитатами.",

    depth_title: "Маркеры глубины",
    depth_lede: "То, что делает это системой, а не демкой — каждый пункт с пруфом, который можно открыть.",
    depth_eval_t: "Eval в CI",
    depth_eval_d: "Golden-набор из 41 кейса с гейтом в GitHub Actions и порогами качества (faithfulness, покрытие цитатами, guardrail, числовая точность).",
    depth_eval_p: "прогоны eval.yml →",
    depth_a2a_t: "Подключаемые A2A-ноды",
    depth_a2a_d: "Воркеры говорят на одном A2A-контракте, так что вторая нода — одна запись в конфиге; round-robin с приоритетом локальной ноды при failover. Публичное демо гоняет один локальный воркер.",
    depth_a2a_p: "T-031 · config/workers.yaml",
    depth_mcp_t: "MCP-инструменты",
    depth_mcp_d: "sql_query, rag_search и price_enrich — MCP-серверы: заменяемы, покрыты контракт-тестами, вызываются любым агентом.",
    depth_mcp_p: "T-027 · MCP-серверы и клиенты",
    depth_route_t: "Многоуровневый роутинг LLM",
    depth_route_d: "Дешёвый/локальный CPU для классификации/извлечения/guard, облачный API для плана/синтеза — провайдер-агностично за одним интерфейсом, с учётом стоимости.",
    depth_route_p: "бенчмарк инференса →",
    depth_guard_t: "Guardrail groundedness",
    depth_guard_d: "Guardrail non-advice блокирует ответы в форме рекомендаций, а проверка groundedness срезает синтез, не подкреплённый извлечённым контекстом.",
    depth_guard_p: "T-022 · T-041",
    depth_obs_t: "Наблюдаемость",
    depth_obs_d: "Шаги, токены, стоимость, латентность и разбивка local-vs-cloud каждого рана ложатся в Postgres и видны в Grafana (read-only роль).",
    depth_obs_p: "борд качества Grafana →",

    bench_title: "Бенчмарки, измеренные",
    bench_lede: "Выбор роутинга и хранилища подкреплён живыми числами, а не ощущениями.",
    bench_inf_t: "Инференс — обоснование роутинга",
    bench_col_model: "Модель",
    bench_col_cost: "Цена /1k",
    bench_col_quality: "Судья",
    bench_inf_take:
      "Flash примерно в 10× дешевле и в 4× быстрее до первого токена — поэтому он берёт роутинг/извлечение/guard; pro-тир зарезервирован под планирование и синтез.",
    bench_vec_t: "Векторное хранилище — pgvector vs Qdrant",
    bench_col_store: "Хранилище",
    bench_col_lat: "Латентность p50",
    bench_vec_take:
      "Recall равный; Qdrant заметно быстрее на запросах и даёт нативный hybrid (dense + BM25) — поэтому нарративный поиск живёт там, а факты остаются в Postgres.",

    close_title: "Запустите сами",
    close_lede:
      "Клонируйте, задайте несколько env-переменных, и make demo поднимает весь стек с данными за минуты — без обращения к EDGAR.",

    footer_disclaimer:
      "LedgerLens анализирует публичную отчётность и рыночные данные. Это не инвестиционный советник: без рекомендаций купить/продать/держать, без ценовых таргетов и персональных советов. Проверяйте цифры по указанному первоисточнику перед использованием.",
    footer_licensing:
      "Данные: SEC EDGAR (fair-use, с User-Agent и лимитами) · MOEX ISS (только для ознакомления / демонстрации). Полные условия — в репозитории.",
    footer_demo: "Живое демо",
  },
};

function applyLang(lang) {
  const dict = I18N[lang] || I18N.en;
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (dict[key] != null) el.textContent = dict[key];
  });
  document.querySelectorAll(".lang-toggle button").forEach((b) => {
    b.classList.toggle("active", b.dataset.lang === lang);
  });
  try {
    localStorage.setItem("ll_lang", lang);
  } catch (e) {
    /* private mode — ignore */
  }
  // refresh the architecture tooltip if one is pinned
  archTipDefault();
}

/* architecture tooltip */
let archLang = "en";
function archTipDefault() {
  const tip = document.getElementById("archTip");
  if (tip) tip.textContent = (I18N[archLang] || I18N.en).tip_default;
}

document.addEventListener("DOMContentLoaded", () => {
  // language: saved → browser → en
  let lang = "en";
  try {
    lang = localStorage.getItem("ll_lang") || (navigator.language || "").slice(0, 2);
  } catch (e) {
    /* ignore */
  }
  if (!I18N[lang]) lang = "en";
  archLang = lang;
  applyLang(lang);

  document.querySelectorAll(".lang-toggle button").forEach((b) => {
    b.addEventListener("click", () => {
      archLang = b.dataset.lang;
      applyLang(b.dataset.lang);
    });
  });

  // architecture hover tooltips
  const tip = document.getElementById("archTip");
  document.querySelectorAll(".arch-node").forEach((node) => {
    const key = node.getAttribute("data-tip");
    node.addEventListener("mouseenter", () => {
      const dict = I18N[archLang] || I18N.en;
      if (tip && dict[key]) tip.textContent = dict[key];
    });
    node.addEventListener("mouseleave", archTipDefault);
  });
});
