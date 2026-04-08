import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vio Vision Demo",
  description: "Vio Vision Demo – football match analysis dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="h-screen flex flex-col bg-brand-bg text-brand-text">
        {children}
      </body>
    </html>
  );
}
