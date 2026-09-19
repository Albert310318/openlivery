import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/app-shell";
import { ToastProvider } from "@/components/toast";
import { LanguageProvider } from "@/lib/i18n";

export const metadata: Metadata = {
  title: "AYV — Atiende y Vende",
  description: "Atiende y Vende: inteligencia aplicada a las ventas para atender conversaciones, captar oportunidades y dar seguimiento comercial.",
  icons: {
    icon: "/icon.svg",
    apple: "/icon.svg",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>
        <LanguageProvider>
          <ToastProvider>
            <AppShell>{children}</AppShell>
          </ToastProvider>
        </LanguageProvider>
      </body>
    </html>
  );
}
