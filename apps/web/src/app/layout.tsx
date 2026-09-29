import type { Metadata, Viewport } from "next";
import { IBM_Plex_Sans, Newsreader } from "next/font/google";
import "./globals.css";

// Plex for the interface and every amount (true tabular figures, narrow punctuation, has ₹);
// Newsreader only for page titles and headline figures, like a printed bank statement.
const plex = IBM_Plex_Sans({ subsets: ["latin", "latin-ext"], weight: ["400", "500", "600"], variable: "--font-plex", display: "swap" });
const newsreader = Newsreader({ subsets: ["latin", "latin-ext"], weight: ["400", "500", "600"], variable: "--font-newsreader", display: "swap" });

export const metadata: Metadata = {
  title: { default: "OneLedger", template: "%s · OneLedger" },
  description: "Your private ledger for every account, card, loan and investment.",
  robots: { index: false, follow: false },
  applicationName: "OneLedger",
  appleWebApp: { capable: true, title: "OneLedger", statusBarStyle: "default" },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f3f4ef" },
    { media: "(prefers-color-scheme: dark)", color: "#0e1522" },
  ],
};

const themeScript = `try{var t=localStorage.getItem("ol-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-IN" className={`${plex.variable} ${newsreader.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
