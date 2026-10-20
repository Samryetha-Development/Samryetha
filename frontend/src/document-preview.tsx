import { LakoButton } from "@lako/ui/components";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useI18n } from "./lib/i18n";

type DocumentState =
  | { status: "loading" }
  | { status: "unsupported" }
  | { status: "failed"; busy: boolean }
  | { status: "ready"; url: string };

export function DocumentPreview({ resourceId, extension }: { resourceId: number; extension: string }) {
  const { t } = useI18n();
  const { user } = useAuth();
  const [state, setState] = useState<DocumentState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let blobUrl = "";
    setState({ status: "loading" });
    void (async () => {
      try {
        const config = await api.files.config();
        if (controller.signal.aborted) return;
        if (!config.documentPreview || !config.convertibleExtensions.includes(extension)) {
          setState({ status: "unsupported" });
          return;
        }
        // Inspect the HTTP result before mounting a PDF iframe: iframe error events do
        // not reliably distinguish a failed conversion from a successful navigation.
        const response = await fetch(`/api/files/resources/${resourceId}/document-preview`, {
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (!response.ok) {
          setState({ status: "failed", busy: response.status === 503 });
          return;
        }
        if (!response.headers.get("content-type")?.startsWith("application/pdf")) {
          setState({ status: "failed", busy: false });
          return;
        }
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        blobUrl = URL.createObjectURL(blob);
        setState({ status: "ready", url: blobUrl });
      } catch {
        if (!controller.signal.aborted) setState({ status: "failed", busy: false });
      }
    })();
    return () => {
      controller.abort();
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [resourceId, extension, user?.id, attempt]);

  if (state.status === "loading") return <p className="files-muted" role="status">{t("file.docPreviewHint")}</p>;
  if (state.status === "unsupported") return <p className="files-muted">{t("file.previewUnsupported")}</p>;
  if (state.status === "failed") {
    return (
      <div role="status">
        <p className="files-muted">{t(state.busy ? "file.previewBusy" : "file.docPreviewFailed")}</p>
        <LakoButton type="button" variant="secondary" size="sm" onClick={() => setAttempt((value) => value + 1)}>
          {t("common.retry")}
        </LakoButton>
      </div>
    );
  }
  return (
    <>
      <iframe className="files-preview-frame" src={state.url} title={t("file.preview")} />
      <p className="files-muted">
        <a href={state.url} target="_blank" rel="noreferrer">{t("file.openPreviewInTab")}</a>
      </p>
    </>
  );
}
