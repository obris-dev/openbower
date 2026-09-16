"use client";

import { Button, FieldError, Input, Label, Switch } from "@bower/ui";
import { WEBHOOK_LABEL_MAX_LENGTH, WEBHOOK_URL_MAX_LENGTH } from "@bower/api";

import { HeadersEditor } from "../headers-editor";
import { FORM_ID, HEADERS_ID, LABEL_ID, URL_ID, type DestinationForm } from "./use-destination-form";

const LABEL_ERROR_ID = "destination-label-error";
const URL_ERROR_ID = "destination-url-error";

/** The destination's editable settings: name, URL, enabled, and the
 * headers (names shown; the editor replaces the whole set). The
 * footer's Save submits this form by id. */
export function SettingsForm({ form, headerNames }: { form: DestinationForm; headerNames: string[] }) {
  return (
    <form
      id={FORM_ID}
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void form.save();
      }}
      className="space-y-5"
    >
      <div>
        <Label htmlFor={LABEL_ID}>Name</Label>
        <Input
          id={LABEL_ID}
          value={form.label}
          onChange={(event) => form.setLabel(event.target.value)}
          maxLength={WEBHOOK_LABEL_MAX_LENGTH}
          warned={form.attempted && form.labelIssue !== null}
          aria-describedby={form.attempted && form.labelIssue ? LABEL_ERROR_ID : undefined}
          className="mt-1"
        />
        {form.attempted && form.labelIssue && (
          <FieldError id={LABEL_ERROR_ID} tone="warning">
            {form.labelIssue}
          </FieldError>
        )}
      </div>
      <div>
        <Label htmlFor={URL_ID}>URL</Label>
        <Input
          id={URL_ID}
          type="text"
          inputMode="url"
          value={form.url}
          onChange={(event) => form.setUrl(event.target.value)}
          maxLength={WEBHOOK_URL_MAX_LENGTH}
          warned={form.attempted && form.urlIssue !== null}
          invalid={form.urlRefusal !== null}
          aria-describedby={(form.attempted && form.urlIssue) || form.urlRefusal ? URL_ERROR_ID : undefined}
          className="mt-1"
        />
        {form.attempted && form.urlIssue && (
          <FieldError id={URL_ERROR_ID} tone="warning">
            {form.urlIssue}
          </FieldError>
        )}
        {form.urlRefusal && <FieldError id={URL_ERROR_ID}>{form.urlRefusal}</FieldError>}
      </div>
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-foreground">Enabled</p>
          <p className="text-xs text-muted">Paused destinations receive no automatic deliveries; a test still sends.</p>
        </div>
        <Switch checked={form.enabled} onChange={form.setEnabled} aria-label="Enabled" />
      </div>
      {form.replacingHeaders ? (
        <div className="space-y-3">
          {/* The consequence stays in view while the editor is open: the
              stored set (values unreadable, so unrecoverable) is what a
              Save replaces, an empty editor included. */}
          <div className="flex flex-wrap items-start justify-between gap-2">
            {headerNames.length > 0 ? (
              <p className="text-xs text-warning">
                Saving replaces the current headers ({headerNames.join(" | ")}) with the rows below; an empty set
                removes them.
              </p>
            ) : (
              <span />
            )}
            <Button type="button" variant="ghost" size="sm" onClick={form.stopReplacingHeaders}>
              Cancel
            </Button>
          </div>
          <HeadersEditor
            id={HEADERS_ID}
            rows={form.headerRows}
            onChange={form.setHeaderRows}
            problem={form.attempted ? form.headersIssue : null}
            refusal={form.headersRefusal}
          />
        </div>
      ) : (
        <div id={HEADERS_ID} className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm font-medium text-foreground">Headers</p>
            <p className="text-xs text-muted">{headerNames.length > 0 ? headerNames.join(" | ") : "None"}</p>
          </div>
          <Button type="button" variant="secondary" size="sm" onClick={form.startReplacingHeaders}>
            Replace headers
          </Button>
        </div>
      )}
    </form>
  );
}
