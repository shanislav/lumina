import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import "./globals.css";
import NavLinks from "@/components/NavLinks";
import AuthGate from "@/components/AuthGate";

export const metadata: Metadata = {
  title: "Lumina",
  description: "DDL and torrent content download orchestrator",
  icons: {
    icon: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="cs" className="dark">
      <body className="min-h-screen antialiased">
        <AuthGate>
          <nav className="border-b border-zinc-800/50 px-4 py-3">
            <div className="max-w-7xl mx-auto flex items-center justify-between gap-3">
              <Link
                href="/"
                className="flex items-center gap-2 text-lg font-bold bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent"
              >
                <Image src="/favicon.svg" alt="" width={24} height={24} />
                <span className="hidden sm:inline">Lumina</span>
              </Link>
              <NavLinks />
            </div>
          </nav>
          {children}
        </AuthGate>
      </body>
    </html>
  );
}
