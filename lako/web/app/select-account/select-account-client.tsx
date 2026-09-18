"use client";

import { LakoSelectAccount, type LakoAccount } from "@lako/ui";

/**
 * lako/web 侧的选号包装。
 *
 * 账号数据由服务端组件取好传进来（同源、转 cookie，没有 CSR 闪烁）；
 * 这里只负责把组件回调接到本应用的导航上。
 */
export function SelectAccountClient({
  account,
  returnTo,
}: {
  account: LakoAccount;
  returnTo: string;
}) {
  const loginHref = `/login?return_to=${encodeURIComponent(returnTo)}`;

  async function forget() {
    // 同源，所以 `lako_csrf` 读得到——跨源宿主做不到这件事，
    // 那条路走的是 onForgetAccount 不传、菜单不渲染。
    const token = decodeURIComponent(
      document.cookie.split("; ").find((item) => item.startsWith("lako_csrf="))?.split("=")[1] ?? "",
    );
    const response = await fetch("/api/sessions/logout", {
      method: "POST",
      headers: { "x-csrf-token": token },
    });
    if (response.ok) location.href = loginHref;
  }

  return (
    <LakoSelectAccount
      account={account}
      subtitle="Continue to Samryetha."
      onSuccess={() => {
        location.href = returnTo;
      }}
      onUseAnotherAccount={() => {
        location.href = loginHref;
      }}
      onCreateAccount={() => {
        location.href = `/register?return_to=${encodeURIComponent(returnTo)}`;
      }}
      onForgetAccount={forget}
    />
  );
}
