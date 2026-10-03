import AppShell from "@/components/mobile/AppShell";
import type { Metadata, Viewport } from "next";
import "./globals.css";
import AuthGate from "@/components/AuthGate";

export const metadata: Metadata = {
  title: "Lumina",
  description: "DDL and torrent content download orchestrator",
  icons: {
    icon: "/favicon.svg",
  },
};

// the phone version uses the whole screen (safe areas around a notch / the home bar)
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", themeColor: "#09090b" };

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="cs" className="dark">
      <body className="min-h-screen antialiased">
        <AuthGate>
          <AppShell>{children}</AppShell>
        </AuthGate>
      </body>
    </html>
  );
}
