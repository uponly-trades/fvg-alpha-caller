import "./globals.css";

export const metadata = { title: "FVG Live", description: "FVG live trading dashboard" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}
