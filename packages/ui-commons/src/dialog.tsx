import * as RadixDialog from "@radix-ui/react-dialog";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { cx } from "./cx.js";

/** Radix Portal 不参与 SSR：首屏 open 必须为 false，否则 hydration 不匹配。 */
export type DialogContentProps = Omit<ComponentPropsWithoutRef<typeof RadixDialog.Content>, "className" | "children">;

export type DialogProps = {
  /** 受控开关。**服务端首次渲染必须为 false**（见文件头注释）。 */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** 非受控用法：传一个触发元素，组件自己包一层 Trigger。 */
  trigger?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  /** 主体内容（表单等），渲染在标题/描述之后、动作行之前。 */
  children?: ReactNode;
  /** 动作按钮，渲染进 .dialog-actions。 */
  actions?: ReactNode;
  /** 错误行，渲染成 .dialog-error。 */
  error?: ReactNode;
  /**
   * 是否允许 Esc 与点击遮罩关闭，默认 true。
   * 对「用户必须显式选择」的框（如只读的临时密码）传 false。
   */
  dismissible?: boolean;
  /** 追加到 .dialog-content 上的类（如 feedback-modal / tasks-modal）。 */
  contentClassName?: string;
  contentProps?: DialogContentProps;
};

export function Dialog({ open, onOpenChange, trigger, title, description, children, actions, error, dismissible = true, contentClassName, contentProps }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      {trigger ? <RadixDialog.Trigger asChild>{trigger}</RadixDialog.Trigger> : null}
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="dialog-overlay" />
        <RadixDialog.Content
          className={cx("dialog-content", contentClassName)}
          // aria-describedby 由 Radix 按「有没有渲染 Description」自己处理，不用管。
          onEscapeKeyDown={dismissible ? undefined : (event) => event.preventDefault()}
          onPointerDownOutside={dismissible ? undefined : (event) => event.preventDefault()}
          onInteractOutside={dismissible ? undefined : (event) => event.preventDefault()}
          {...contentProps}
        >
          <RadixDialog.Title className="dialog-title">{title}</RadixDialog.Title>
          {description ? <RadixDialog.Description className="dialog-description">{description}</RadixDialog.Description> : null}
          {error ? <div className="dialog-error">{error}</div> : null}
          {children}
          {actions ? <div className="dialog-actions">{actions}</div> : null}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
