import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge Tailwind classes with deduplication.
 * Usage: `cn("px-4 py-2", condition && "bg-red-500", "px-6")`
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
