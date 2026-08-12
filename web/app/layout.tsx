import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Quran Studio",
  description: "Overlay Quran recitation on cartoons, or strip background music.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
