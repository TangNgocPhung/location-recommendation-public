import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Nearby — Khám phá địa điểm quanh bạn',
  description:
    'MVP tìm kiếm và xếp hạng địa điểm theo vị trí, khoảng cách và đánh giá.',
  // Cần cho Proximity Notification Service: Service Worker và quyền thông báo
  // chỉ hoạt động trong secure context, và manifest là thứ biến trang thành một
  // ứng dụng cài được thay vì một tab bình thường.
  manifest: '/manifest.json',
  themeColor: '#0f8a62',
  icons: { icon: '/icon-192.png', apple: '/icon-192.png' },
};

// Chạy đồng bộ trước khi React hydrate: đặt sẵn class '.dark' theo lựa chọn đã
// lưu / theme hệ thống nên không nháy sáng->tối và <html> khớp trạng thái client.
const themeScript = `(function(){try{var t=localStorage.getItem('nearby-theme');if(t!=='dark'&&t!=='light'){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}if(t==='dark'){document.documentElement.classList.add('dark');}}catch(e){}})();`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="vi" suppressHydrationWarning>
      <body>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        {children}
      </body>
    </html>
  );
}
