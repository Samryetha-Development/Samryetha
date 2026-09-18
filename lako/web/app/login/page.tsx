"use client";

import { LakoLogin } from "@lako/ui";

/**
 * `return_to` 只认站内授权续跳，否则回账号页。
 * 这是 lako/web 作为 OIDC 宿主的防线；组件本身不再接触 `return_to`。
 */
function safeReturnTo(value: string | null) {
  return value?.startsWith("/oauth/authorize?") ? value : "/account";
}

export default function Login() {
  function finish() {
    location.href = safeReturnTo(new URLSearchParams(location.search).get("return_to"));
  }

  return (
    <main className="lako-auth lako-auth-main">
      <LakoLogin onSuccess={finish} subtitle="Continue to Samryetha." registerHref="/register" resetHref="/reset" />
    </main>
  );
}
