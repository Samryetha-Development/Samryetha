/** 拼 class 名：丢掉 false / null / undefined / 空串。 */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
