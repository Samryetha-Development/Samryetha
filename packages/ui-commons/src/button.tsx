import type { ComponentPropsWithRef } from "react";

import { cx } from "./cx.js";

/**
 * variant 名刻意对齐 design.md §7.1 的按钮层级，映射到宿主已有的 class 名。
 * 本期只做「行为与结构收敛」：组件产出的 class 与手写时完全一致，外观零变化。
 */
export type ButtonVariant = "primary" | "secondary" | "danger" | "admin" | "login" | "compose" | "icon";

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary: "primary-action", // 实心胶囊，页面的主 CTA
  secondary: "action-btn", // 描边胶囊，默认层级
  danger: "dialog-danger", // 实心红胶囊；design.md:230 规定这是唯一允许的红填充
  admin: "admin-btn", // 方角 8px，密集的管理区
  login: "login-primary", // 登录表单的整宽提交
  compose: "compose", // 顶栏发帖
  icon: "icon-btn", // 圆形 40×40
};

export type ButtonTone = "default" | "danger";

export type ButtonClassOptions = {
  variant?: ButtonVariant;
  /** 描边层级下的危险语义（.action-btn.danger / .admin-btn.danger）。 */
  tone?: ButtonTone;
  /** 选中/展开态（.action-btn.active）。 */
  active?: boolean;
  className?: string;
};

/** 给非 <button> 元素（如 <a>）复用同一套 class，避免为此引入 Radix Slot 依赖。 */
export function buttonClass({ variant = "secondary", tone = "default", active = false, className }: ButtonClassOptions = {}): string {
  return cx(VARIANT_CLASS[variant], active && "active", tone === "danger" && "danger", className);
}

export type ButtonProps = ComponentPropsWithRef<"button"> & ButtonClassOptions;

/**
 * 注意：**不**给 `type` 设默认值。手写的按钮有相当一部分在表单里靠 HTML 默认的
 * "submit" 行为工作，擅自改成 "button" 会让它们静默失效。
 */
export function Button({ variant, tone, active, className, ...rest }: ButtonProps) {
  return <button className={buttonClass({ variant, tone, active, className })} {...rest} />;
}
