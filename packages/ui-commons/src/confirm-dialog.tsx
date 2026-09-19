import * as RadixAlertDialog from "@radix-ui/react-alert-dialog";
import type { ReactNode } from "react";

import { Button } from "./button.js";
import { cx } from "./cx.js";

export type ConfirmDialogProps = {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** 非受控用法：传一个触发元素。 */
  trigger?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel: ReactNode;
  cancelLabel: ReactNode;
  onConfirm: () => void;
  /** 确认按钮用实心红（破坏性操作）。默认 true。 */
  danger?: boolean;
  /** 操作进行中：两个按钮都禁用。 */
  pending?: boolean;
  /**
   * 确认按钮不用 AlertDialog.Action（后者点击即关闭弹窗），改为普通按钮。
   * 用于「异步操作期间必须保持弹窗打开、失败要留在原地显示错误」的场景：
   * 此时关闭时机由调用方通过 open/onOpenChange 自己控制。
   */
  stayOpen?: boolean;
  contentClassName?: string;
};

/**
 * 破坏性确认。用 AlertDialog 而不是 Dialog：它刻意不响应点击遮罩，
 * 迫使调用方在「取消」和「确认」之间显式选择，避免误触关闭。
 * 焦点默认落在取消上。
 */
export function ConfirmDialog({ open, onOpenChange, trigger, title, description, confirmLabel, cancelLabel, onConfirm, danger = true, pending = false, stayOpen = false, contentClassName }: ConfirmDialogProps) {
  return (
    <RadixAlertDialog.Root open={open} onOpenChange={onOpenChange}>
      {trigger ? <RadixAlertDialog.Trigger asChild>{trigger}</RadixAlertDialog.Trigger> : null}
      <RadixAlertDialog.Portal>
        <RadixAlertDialog.Overlay className="dialog-overlay" />
        <RadixAlertDialog.Content className={cx("dialog-content", contentClassName)}>
          <RadixAlertDialog.Title className="dialog-title">{title}</RadixAlertDialog.Title>
          {description ? <RadixAlertDialog.Description className="dialog-description">{description}</RadixAlertDialog.Description> : null}
          <div className="dialog-actions">
            <RadixAlertDialog.Cancel asChild>
              <Button variant="secondary" type="button" disabled={pending}>
                {cancelLabel}
              </Button>
            </RadixAlertDialog.Cancel>
            {stayOpen ? (
              <Button variant={danger ? "danger" : "primary"} type="button" disabled={pending} onClick={onConfirm}>
                {confirmLabel}
              </Button>
            ) : (
              <RadixAlertDialog.Action asChild>
                <Button variant={danger ? "danger" : "primary"} type="button" disabled={pending} onClick={onConfirm}>
                  {confirmLabel}
                </Button>
              </RadixAlertDialog.Action>
            )}
          </div>
        </RadixAlertDialog.Content>
      </RadixAlertDialog.Portal>
    </RadixAlertDialog.Root>
  );
}
