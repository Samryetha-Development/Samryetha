import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { AccountChoice } from "./account-choice";

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
  const cookieStore = await cookies();
  const response = await fetch(`${api}/api/auth/me`, {
    headers: { cookie: cookieStore.toString() },
    cache: "no-store",
  });
  if (!response.ok) redirect(`/login?return_to=${encodeURIComponent(returnTo)}`);
  const account = (await response.json()) as Account;
  const initial = (account.display_name || account.username).slice(0, 1).toUpperCase();

  return <main className={`auth-main${embedded ? " auth-embedded" : ""}`}><section className="auth-wrap auth-login"><div className="panel auth-panel account-chooser"><div className="auth-product"><span className="identity-glyph" aria-hidden="true"><i /><i /><i /></span>Lako</div><h1>Choose an account</h1><p className="subtle">Continue to Samryetha.</p><div className="account-options"><AccountChoice displayName={account.display_name} username={account.username} email={account.email} initial={initial} returnTo={returnTo} embedded={embedded} /><a className="account-action" href={`/login?return_to=${encodeURIComponent(returnTo)}`} target="_top">Use another account</a><a className="account-action" href={`/register?return_to=${encodeURIComponent(returnTo)}`} target="_top">Create account</a></div></div></section></main>;
}
