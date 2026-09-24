import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Project Assistant",
  description: "Local-first project intelligence with multi-repo RAG, knowledge graph and guided code generation",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en-AU">
      <body>{children}</body>
    </html>
  );
}
