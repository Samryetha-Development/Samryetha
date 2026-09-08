// 通用加载占位：转圈小圆圈 + 一行说明。凡是等数据的地方都用它，
// 代替原来生硬的纯文本。
import { useI18n } from "./lib/i18n";

export function Loading({ label }: { label?: string }) {
  const { t } = useI18n();
  return (
    <div className="loading-state" role="status">
      <span className="spinner" aria-hidden="true" />
      <span className="loading-label">{label ?? t("common.loading")}</span>
    </div>
  );
}
