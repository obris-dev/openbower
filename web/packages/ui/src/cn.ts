import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * The ONLY className combiner in the workspace. clsx handles the
 * conditional shapes; twMerge resolves Tailwind conflicts so a caller's
 * override (`py-0`) actually beats a primitive's base (`py-1`) instead
 * of losing to stylesheet order. Never merge with bare clsx or template
 * strings.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
