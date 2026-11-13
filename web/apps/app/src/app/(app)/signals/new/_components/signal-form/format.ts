// Amounts as a person reads them, shared by the suggested name and the
// preview's rows.

/** An amount compact in its own currency ($5.2M, €40M); the bare number
 * when no currency is given, and the number then the code when the code
 * is one Intl does not know. */
export function compactAmount(amount: number, currency: string | null): string {
  const bare = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(amount);
  if (currency === null) return bare;
  try {
    const options = { style: "currency", currency, notation: "compact", maximumFractionDigits: 1 } as const;
    return new Intl.NumberFormat("en-US", options).format(amount);
  } catch (error) {
    if (error instanceof RangeError) return `${bare} ${currency}`;
    throw error;
  }
}
