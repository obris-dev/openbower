"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Button,
  CopyButton,
  ErrorMessage,
  Input,
  Label,
  Popover,
  PopoverButton,
  PopoverPanel,
  Skeleton,
  useToast,
} from "@bower/ui";
import {
  fetchSignalIngestSchema,
  fetchSignalWebhook,
  rotateSignalWebhook,
  WEBHOOK_UNREADABLE_CODE,
  webRoutes,
  type IngestColumn,
} from "@bower/api";

import { ConfirmDelete } from "../../_components/confirm-delete";
import { PUSH_CREDENTIAL, PushConnect } from "../../_components/push-connect";
import { ROTATE_URL, ROTATE_URL_CONSEQUENCE, ROTATE_URL_QUESTION, ROTATED_TOAST, WEBHOOK_LEDE, WEBHOOK_TITLE } from "./copy";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";

const URL_ID = "signal-webhook-url";
const TITLE_ID = "signal-webhook-title";

// The URL (null while it cannot be shown), and the columns its sender sets.
type Loaded = { url: string | null; columns: IngestColumn[] };

/** A custom signal's connection: its webhook URL to copy any time, with
 * Rotate beside Copy for a URL that leaked, then the command and row keys
 * its sender needs (the signal's own push schema). Both reads go out
 * together. */
export function WebhookConnection({ signalId }: { signalId: string }) {
  const toast = useToast();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [rotating, setRotating] = useState(false);
  const [rotateRefusal, setRotateRefusal] = useState<string | null>(null);
  const panelOpen = useRef(false);

  useEffect(() => {
    let current = true;
    async function load() {
      const [webhook, schema] = await Promise.all([fetchSignalWebhook(signalId), fetchSignalIngestSchema(signalId)]);
      if (!current) return;
      if (redirectIfUnauthenticated(webhook) || redirectIfUnauthenticated(schema)) return;
      if (schema.status !== "ok") {
        setFailure(schema.message);
        return;
      }
      if (webhook.status === "ok") {
        setLoaded({ url: webhook.data.webhook_url, columns: schema.data.columns });
        return;
      }
      setFailure(webhook.message);
      // A URL that can't be shown is still rotatable: keep the columns so
      // the rotate's answer fills the section in.
      if (webhook.code === WEBHOOK_UNREADABLE_CODE) setLoaded({ url: null, columns: schema.data.columns });
    }
    void load();
    return () => {
      current = false;
    };
  }, [signalId]);

  // A refusal stays in the panel, where the person asked; success closes
  // it, and the panel hands focus back to Rotate URL.
  async function rotate(close: () => void) {
    setRotating(true);
    setRotateRefusal(null);
    const res = await rotateSignalWebhook(signalId);
    setRotating(false);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "error") {
      // An outside click or Escape can close the panel mid-request: the
      // refusal then has nowhere to show but a toast.
      if (panelOpen.current) setRotateRefusal(res.message);
      else toast.error(res.message);
      return;
    }
    close();
    setFailure(null);
    setLoaded((prev) => (prev === null ? prev : { ...prev, url: res.data.webhook_url }));
    toast.success(ROTATED_TOAST);
  }

  return (
    <section aria-labelledby={TITLE_ID} className="space-y-6">
      <div>
        {/* Labels the URL field only while there is one (an unreadable URL
            shows its message and Rotate instead). */}
        <Label id={TITLE_ID} htmlFor={loaded?.url ? URL_ID : undefined}>
          {WEBHOOK_TITLE}
        </Label>
        <p className="text-xs text-muted">{WEBHOOK_LEDE}</p>
        {failure && <ErrorMessage message={failure} />}
        {!loaded && !failure && <Skeleton className="mt-2 h-9" />}
        {loaded && (
          <div className="mt-2 flex items-center gap-2">
            {loaded.url !== null && (
              <>
                <Input
                  id={URL_ID}
                  readOnly
                  value={loaded.url}
                  onFocus={(e) => e.currentTarget.select()}
                  className="font-mono text-xs"
                />
                <CopyButton value={loaded.url} />
              </>
            )}
            {/* The question hangs off the verb in an anchored panel, the
                API keys roster's pattern: the row never changes shape. */}
            <Popover className="relative inline-block shrink-0">
              <PopoverButton as={Button} variant="ghost" size="sm" className="whitespace-nowrap text-danger">
                {ROTATE_URL}
              </PopoverButton>
              <PopoverPanel focus>
                {({ close }) => (
                  <RotatePanel
                    onOpen={() => {
                      panelOpen.current = true;
                      setRotateRefusal(null);
                    }}
                    onClose={() => {
                      panelOpen.current = false;
                    }}
                  >
                  <ConfirmDelete
                    verb="Rotate"
                    question={ROTATE_URL_QUESTION}
                    consequence={ROTATE_URL_CONSEQUENCE}
                    busy={rotating}
                    refusal={rotateRefusal}
                    onCancel={() => {
                      setRotateRefusal(null);
                      close();
                    }}
                    onDelete={() => void rotate(close)}
                  />
                  </RotatePanel>
                )}
              </PopoverPanel>
            </Popover>
          </div>
        )}
      </div>
      {!loaded && !failure && <Skeleton className="h-32" />}
      {loaded && loaded.url !== null && (
        <PushConnect
          key={loaded.url}
          url={loaded.url}
          columns={loaded.columns}
          credential={PUSH_CREDENTIAL.IN_URL}
          sheetHref={webRoutes.sheet(signalId)}
        />
      )}
    </section>
  );
}

/** The rotate panel's body, mounted only while the panel is open: it tells
 * the section when it opens (a fresh question, no stale refusal) and when
 * it closes (a late refusal goes to a toast instead). */
function RotatePanel({ onOpen, onClose, children }: { onOpen: () => void; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    onOpen();
    return onClose;
    // Mount and unmount only: the panel opening and closing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <>{children}</>;
}
