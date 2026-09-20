import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Project Assistant",
  description: "Local-first GPT workbench with RAG, knowledge graph and approval-gated changes",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en-AU">
      <body>{children}</body>
    </html>
  );
}
