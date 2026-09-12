// Service Worker của Nearby — chỉ làm đúng phần mà trang không tự làm được.
//
// Không cache gì cả. Một SW cache sai là lỗi rất khó chẩn đoán: người dùng thấy
// bản cũ của ứng dụng, hard-refresh không ăn thua, và nguyên nhân nằm ở một
// tệp mà không ai nhớ là có tồn tại. Ở đây SW tồn tại vì đúng một lý do:
// `registration.showNotification()` hiện được thông báo cả khi tab đang ở nền,
// còn `new Notification()` dựng từ trang thì bị nhiều trình duyệt chặn.
//
// Không dùng Web Push: thông báo đến qua SSE trong tab đang mở rồi được
// postMessage sang đây. Đổi lại là tab phải mở — đánh đổi có chủ ý, ghi rõ
// trong báo cáo, vì Web Push cần VAPID key và một máy chủ đẩy của bên thứ ba.

self.addEventListener('install', () => {
  // Nhận quyền ngay, không chờ tab cũ đóng.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('message', (event) => {
  const data = event.data;
  if (!data || data.type !== 'nearby:proximity') return;

  const payload = data.payload || {};
  const title = payload.title || 'Địa điểm đã lưu';
  const distance =
    typeof payload.distanceMeters === 'number'
      ? `${Math.round(payload.distanceMeters)} m`
      : 'gần đây';

  event.waitUntil(
    self.registration.showNotification(`Bạn đang ở gần ${title}`, {
      body: `Cách khoảng ${distance}${payload.address ? ` · ${payload.address}` : ''}`,
      // `tag` gộp thông báo trùng của cùng một địa điểm thành một, thay vì xếp
      // chồng. Backend đã có cooldown, nhưng hai tab cùng mở sẽ nhận cùng một
      // sự kiện SSE — đây là lớp chặn thứ hai, ở phía trình duyệt.
      tag: `nearby-proximity-${payload.subscriptionId || payload.poiId || title}`,
      renotify: false,
      data: { poiId: payload.poiId },
      icon: '/icon-192.png',
      badge: '/icon-192.png',
    }),
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const poiId = event.notification.data && event.notification.data.poiId;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      // Ưu tiên đưa tab đang mở lên trước rồi báo cho nó chọn POI, thay vì mở
      // thêm một tab mới: mở tab mới làm mất trạng thái tìm kiếm hiện tại.
      for (const client of clients) {
        if ('focus' in client) {
          client.postMessage({ type: 'nearby:focus-poi', poiId });
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(poiId ? `/?poi=${poiId}` : '/');
      }
      return undefined;
    }),
  );
});
