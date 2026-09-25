import Link from "next/link";

import { Wordmark } from "@/components/layout/logo";
import { ThemeToggle } from "@/components/layout/theme-toggle";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-surface-sunken">
      <header className="flex items-center justify-between px-6 py-5">
        <Link href="/" className="rounded-md">
          <Wordmark />
        </Link>
        <ThemeToggle />
      </header>

      <main className="flex flex-1 items-center justify-center px-6 pb-20">
        <div className="w-full max-w-[400px]">{children}</div>
      </main>

      <footer className="flex justify-center px-6 py-6">
        <Wordmark className="opacity-60" />
      </footer>
    </div>
  );
}
