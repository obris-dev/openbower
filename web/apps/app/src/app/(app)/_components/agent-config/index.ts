// The agent-config editor: the surfaces and grammars for composing an
// agent's prompt, model, and outputs. Shared, because two routes
// compose the same config: the agents builder page, and the sheet's
// AI-column drawer. It lived inside the builder leaf while the builder
// was its only consumer; a second one is what moves it here.
//
// Everything the family needs from itself it imports by file; this
// index is what the REST of the app may reach for.
export { ModelPicker } from "./model-picker";
export { EMPTY_OUTPUT, OutputsEditor } from "./outputs-editor";
export { PromptEditor } from "./prompt-editor";
export { ReadinessChecklist } from "./readiness-checklist";
export { ToolToggles } from "./tool-toggles";
export { AGENT_TOOLS, EMPTY_TOOLS } from "./tools-meta";
export { draftEquals, draftProvider, saveShape, useAgentDraft, type Draft, type Provider } from "./lib/draft";
export { isContentful, outputKey, outputsProblem } from "./lib/output-key";
export { modelsTruncatedNote, sheetsTruncatedNote } from "./copy";
export {
  buildChecklist,
  configMissing,
  configReady,
  firstGap,
  goToSection,
  type Attempt,
  type ChecklistItem,
  type Missing,
  type SectionKey,
} from "./lib/readiness";
export { promptVariables, stripVariable } from "./lib/template";
