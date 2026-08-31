import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Statement Intel",
  description: "Bank statement extraction, verification and reconciliation",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-slate-950 text-slate-100 antialiased">{children}</body>
    </html>
  );
}
