import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Toaster } from "sonner";
import "./globals.css";

export const metadata: Metadata = {
  title: "ADAM · Personal OS",
  description: "ADAM — your sovereign Personal OS: mail, chats, contacts, tasks, and budget on your own server",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`dark ${GeistSans.variable} ${GeistMono.variable}`}
      suppressHydrationWarning
    >
      <body className="antialiased">
        {children}
        <Toaster theme="dark" position="bottom-right" />
      </body>
    </html>
  );
}
