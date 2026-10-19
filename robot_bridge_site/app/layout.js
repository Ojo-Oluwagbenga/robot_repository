import "./globals.css";

export const metadata = {
  title: "Nova Robot Bridge - Realtime DB",
  description: "Minimalist Realtime Database Bridge connecting Gemini Live and Raspberry Pi",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
