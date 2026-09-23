import type { RefObject } from "react";

import { useI18n, type I18nKey } from "./lib/i18n";

export type PrimaryView = "latest" | "followed" | "boards";
export type TopNavLocation = "feedback";

type NavIndicator = {
  ready: boolean;
  animate: boolean;
  width: number;
  x: number;
};

export const TOP_NAV_LINKS: ReadonlyArray<{
  href: string;
  view?: PrimaryView;
  labelKey: I18nKey;
}> = [
  { href: "/", view: "latest", labelKey: "nav.latest" },
  { href: "/", view: "followed", labelKey: "nav.followed" },
  { href: "/", view: "boards", labelKey: "nav.boards" },
  { href: "/feedback", labelKey: "nav.feedback" },
];

export function TopNav({
  current,
  activeView,
  onViewChange,
  indicator,
  navRef,
}: {
  current?: TopNavLocation;
  activeView?: PrimaryView;
  onViewChange?: (view: PrimaryView) => void;
  indicator?: NavIndicator;
  navRef?: RefObject<HTMLElement | null>;
}) {
  const { t } = useI18n();
  const interactive = activeView !== undefined && onViewChange !== undefined;

  return (
    <nav
      className={`primary-nav ${interactive ? "" : "primary-nav-static"}`}
      aria-label={t("nav.primary")}
      ref={navRef}
    >
      {TOP_NAV_LINKS.map((link) => {
        if (link.view && interactive) {
          const active = activeView === link.view;
          return (
            <button
              key={link.view}
              data-view={link.view}
              className={`nav-link ${active ? "active" : ""}`}
              type="button"
              aria-current={active ? "page" : undefined}
              onClick={() => onViewChange(link.view!)}
            >
              {t(link.labelKey)}
            </button>
          );
        }

        const active = !link.view && current !== undefined && link.href === `/${current}`;
        return (
          <a
            key={`${link.href}-${link.view ?? "page"}`}
            className={`nav-link ${active ? "active" : ""}`}
            href={link.href}
            data-view={link.view}
            aria-current={active ? "page" : undefined}
          >
            {t(link.labelKey)}
            {active ? <span className="nav-indicator nav-indicator-static ready" aria-hidden="true" /> : null}
          </a>
        );
      })}
      {interactive && indicator ? (
        <span
          className={`nav-indicator${indicator.ready ? " ready" : ""}${indicator.animate ? " animate" : ""}`}
          style={{ width: indicator.width, transform: `translateX(${indicator.x}px)` }}
          aria-hidden="true"
        />
      ) : null}
    </nav>
  );
}
