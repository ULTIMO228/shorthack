import type { Metadata } from "next";
import { AuthProvider } from "@/components/auth-provider";
import { ToastProvider } from "@/components/toast";
import Shell from "@/components/shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "MAIA — ИИ-помощник МИСИС",
  description:
    "MAIA — ИИ-помощник поддержки НИТУ МИСИС: приём обращений, заказ справок, статус сервисов и эскалация оператору.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <AuthProvider>
          <ToastProvider>
            <Shell>{children}</Shell>
          </ToastProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
