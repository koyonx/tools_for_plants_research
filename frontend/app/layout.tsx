import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "tools_for_plants_research",
  description: "植物組織画像の全自動解析ツール",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
