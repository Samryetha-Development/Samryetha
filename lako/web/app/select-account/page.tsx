import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { SelectAccountClient } from "./select-account-client";

const api = process.env.LAKO_API_INTERNAL_URL ?? "http://localhost:8000";

type Account = {
  display_name: string;
  username: string;
  email: string;
};

function safeReturnTo(value: string | string[] | undefined): string {
  const candidate = Array.isArray(value) ? value[0] : value;
  return candidate?.startsWith("/oauth/authorize?") ? candidate : "/account";
}

export default async function SelectAccount({
  searchParams,
}: {
  searchParams: Promise<{ return_to?: string | string[]; embedded?: string | string[] }>;
}) {
  const params = await searchParams;
  const returnTo = safeReturnTo(params.return_to);
  const embedded = (Array.isArray(params.embedded) ? params.embedded[0] : params.embedded) === "1";

  // 服务端带 cookie 去问 API，避免客户端取数时的闪烁。
  const cookieStore = await cookies();
  const response = await fetch(`${api}/api/auth/me`, {
    headers: { cookie: cookieStore.toString() },
    cache: "no-store",
  });
  if (!response.ok) redirect(`/login?return_to=${encodeURIComponent(returnTo)}`);
  const account = (await response.json()) as Account;

  return (
    <main className="lako-auth lako-auth-main" data-lako-embedded={embedded ? "" : undefined}>
      <SelectAccountClient account={account} returnTo={returnTo} />
    </main>
  );
}
