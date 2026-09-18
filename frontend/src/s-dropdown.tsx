import type { ReactNode } from "react";
import { Dropdown } from "samryetha-ui-commons";
import { useI18n } from "./lib/i18n";

type SDropdownProps<T> = {
  items: T[];
  value: T | null;
  onChange: (item: T) => void;
  getKey: (item: T) => string | number;
  getLabel: (item: T) => ReactNode;
  placeholder?: ReactNode;
  ariaLabel: string;
  label?: ReactNode;
  className?: string;
  disabled?: boolean;
  getDisabled?: (item: T) => boolean;
};

/**
 * 过渡层，只做一件事：把宿主的 i18n 接进包组件。
 * 包本身不认 useI18n（那是组件库与宿主绑死的一处），所以默认占位文案在这里解析。
 * 等 15 处调用点都改为直接传 placeholder 之后，这个文件即可删除。
 */
export function SDropdown<T>(props: SDropdownProps<T>) {
  const { t } = useI18n();
  return <Dropdown {...props} placeholder={props.placeholder ?? t("common.chooseOption")} />;
}
