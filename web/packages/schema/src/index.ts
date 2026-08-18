// The .ts extension on the import is deliberate (plain-node test lane
// upstream); bundlers resolve it identically.
// @bower/schema: zod validators generated from the shared Pydantic
// contract (see packages/openbower-schema). Run `pnpm --filter @bower/schema generate` after the
// Python side changes.
export {
  AuthUserSchema,
  type AuthUser,
  CompanySchema,
  type Company,
  FoldersListSchema,
  type FoldersList,
  FolderSummarySchema,
  type FolderSummary,
  ImportResultSchema,
  type ImportResult,
  type ListColumn,
  ListRowsPageSchema,
  type ListRowsPage,
  type ListRowWire,
  ListsPageSchema,
  type ListsPage,
  ListSummarySchema,
  type ListSummary,
  type LookalikeGroup,
  LookalikeItemSchema,
  type LookalikeItem,
  LookalikeListResponseSchema,
  type LookalikeListResponse,
  RowsAddedSchema,
  type RowsAdded,
} from "./generated.ts";
