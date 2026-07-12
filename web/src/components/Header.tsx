import type { Lang } from "../i18n";
import { useI18n } from "../i18n";
import type { RunView } from "../types";

// Canonical public repository — the demo banner links back to it (T-036 §5).
const REPO_URL = "https://github.com/zzlawlzz/ledgerlens";

export function Header({ run, mode, demo }: { run: RunView; mode?: string; demo?: boolean }) {
  const { lang, setLang, t } = useI18n();
  const cost = run.summary?.usage.cost_usd;
  return (
    <header className="header">
      <div className="header-titles">
        <h1>{t("title")}</h1>
        <span className="subtitle">{t("subtitle")}</span>
      </div>
      <div className="header-right">
        {cost !== undefined && (
          <span className="budget" data-testid="budget">
            {t("budget_spent")}: ${cost.toFixed(4)}
          </span>
        )}
        <div className="lang-switch" data-testid="lang-switch">
          {(["en", "ru"] as Lang[]).map((code) => (
            <button
              key={code}
              className={lang === code ? "lang active" : "lang"}
              onClick={() => setLang(code)}
            >
              {code.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      <div className="disclaimer" data-testid="disclaimer">
        {t("disclaimer")}
      </div>
      {mode === "ru" && (
        <div className="disclaimer iss-attribution" data-testid="iss-attribution">
          {t("iss_attribution")}
        </div>
      )}
      {demo && (
        <div className="disclaimer demo-banner" data-testid="demo-banner">
          {t("demo_banner")}{" "}
          <a href={REPO_URL} target="_blank" rel="noreferrer">
            {t("demo_repo_link")}
          </a>
        </div>
      )}
    </header>
  );
}
