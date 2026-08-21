import { cn } from "./cn";

export function ErrorMessage({ message, className, id }: { message: string; className?: string; id?: string }) {
  return (
    <div
      role="alert"
      id={id}
      className={cn(
        "rounded-md bg-danger-wash p-3 text-sm text-danger-strong",
        className,
      )}
    >
      {message}
    </div>
  );
}
