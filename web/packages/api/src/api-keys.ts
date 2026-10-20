// Machine API keys: the credential a script pushes rows with. The app
// proxies them to the identity service, so the browser never holds the
// user's upstream token. Same result-union philosophy as lists.

import { ApiKeyMintedSchema, WIRE_CONSTANTS, type ApiKeyMinted, type ApiKeysList, type ApiKeyWire } from "@bower/schema";

import { http, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";

export type { ApiKeyMinted, ApiKeysList, ApiKeyWire };

// The bound the identity service enforces on create; the input stops at it.
export const API_KEY_NAME_MAX_LENGTH = WIRE_CONSTANTS.API_KEY_NAME_MAX_LENGTH;

/** Mint a key. The response carries the token ONCE; no later read
 * returns it. */
export async function mintApiKey(name: string): Promise<ApiResult<ApiKeyMinted>> {
  return http.post(apiRoutes.auth.apiKeys, ApiKeyMintedSchema, { name });
}

export async function revokeApiKey(id: string): Promise<ApiResult<null>> {
  return http.delete(apiRoutes.auth.apiKey(id));
}
