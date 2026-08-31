import type { Metadata } from 'next';
import './globals.css';

const siteOrigin = process.env.NEXT_PUBLIC_SITE_ORIGIN ?? 'http://localhost:3000';

export const metadata: Metadata = {
  metadataBase: new URL(siteOrigin),
  title: 'Veyra — Local agent dashboard',
  description: 'A private command center for your self-evolving local AI agent.',
  openGraph: {
    title: 'Veyra — Local agent dashboard',
    description: 'A private command center for your self-evolving local AI agent.',
    images: [{ url: '/og.png', width: 1200, height: 630, alt: 'Veyra — Your agent, at a glance.' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Veyra — Local agent dashboard',
    description: 'A private command center for your self-evolving local AI agent.',
    images: ['/og.png'],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
