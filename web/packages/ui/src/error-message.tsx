import { cn } from "./cn";

export function ErrorMessage({ message, className, id }: { message: string; className?: string; id?: string }) {
  return (
    <div
      role="alert"
      id={id}
      className={cn(
        "rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300",
        className,
      )}
    >
      {message}
    </div>
  );
}
