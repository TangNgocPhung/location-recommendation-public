'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Proximity Notification Service — phía trình duyệt (lộ trình B13b).
 *
 * Ba mảnh ghép:
 *  1. Service Worker (`/sw.js`) để `showNotification` hiện được cả khi tab ở nền.
 *  2. Quyền thông báo, chỉ xin khi người dùng chủ động bật — xin lúc tải trang
 *     là cách nhanh nhất để bị từ chối vĩnh viễn.
 *  3. Kênh SSE tới `/api/v1/notifications/stream`.
 *
 * **Vì sao không dùng `EventSource`** dù nó có sẵn tự-kết-nối-lại: `EventSource`
 * không gửi được header tuỳ ý, nên `X-Session-ID` sẽ phải nằm trên URL và lọt
 * vào mọi dòng access log của nginx và mọi proxy trên đường đi. Đọc SSE bằng
 * `fetch` + `ReadableStream` giữ định danh phiên trong header, đổi lại phải tự
 * viết phần kết nối lại — khoảng mười dòng ở dưới.
 */

export type ProximityNotification = {
  hitId?: string;
  sessionId: string;
  subscriptionId: string;
  poiId: string;
  title: string;
  category?: string | null;
  address?: string | null;
  latitude?: number;
  longitude?: number;
  distanceMeters: number;
  radiusMeters: number;
};

type Options = {
  apiBaseUrl: string;
  sessionId: string;
  enabled: boolean;
  onNotification?: (notification: ProximityNotification) => void;
};

// Chờ trước khi kết nối lại, tăng dần rồi chặn trần. Không có trần thì một
// backend chết sẽ bị một tab bỏ quên nện liên tục.
const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

export function useProximityNotifications({
  apiBaseUrl,
  sessionId,
  enabled,
  onNotification,
}: Options) {
  const [permission, setPermission] = useState<NotificationPermission | 'unsupported'>(
    () =>
      typeof window === 'undefined' || !('Notification' in window)
        ? 'unsupported'
        : Notification.permission,
  );
  const [connected, setConnected] = useState(false);
  const [lastNotification, setLastNotification] = useState<ProximityNotification | null>(null);
  const registrationRef = useRef<ServiceWorkerRegistration | null>(null);
  const callbackRef = useRef(onNotification);
  callbackRef.current = onNotification;

  /** Đăng ký Service Worker và xin quyền. Gọi từ một cú bấm của người dùng. */
  const requestPermission = useCallback(async () => {
    if (typeof window === 'undefined' || !('Notification' in window)) {
      setPermission('unsupported');
      return false;
    }
    let granted = Notification.permission;
    if (granted === 'default') {
      granted = await Notification.requestPermission();
    }
    setPermission(granted);
    if (granted !== 'granted') return false;

    if ('serviceWorker' in navigator) {
      try {
        registrationRef.current = await navigator.serviceWorker.register('/sw.js');
      } catch {
        // SW hỏng không được chặn tính năng: `new Notification()` vẫn chạy khi
        // tab đang mở, chỉ mất phần hiện thông báo lúc tab ở nền.
        registrationRef.current = null;
      }
    }
    return true;
  }, []);

  const show = useCallback((payload: ProximityNotification) => {
    const registration = registrationRef.current;
    if (registration?.active) {
      registration.active.postMessage({ type: 'nearby:proximity', payload });
      return;
    }
    if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      const distance = Math.round(payload.distanceMeters);
      new Notification(`Bạn đang ở gần ${payload.title}`, {
        body: `Cách khoảng ${distance} m`,
        tag: `nearby-proximity-${payload.subscriptionId}`,
      });
    }
  }, []);

  useEffect(() => {
    if (!enabled || !sessionId) {
      setConnected(false);
      return;
    }

    const controller = new AbortController();
    let stopped = false;
    let backoff = RECONNECT_MIN_MS;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function readStream() {
      const response = await fetch(`${apiBaseUrl}/api/v1/notifications/stream`, {
        headers: { 'X-Session-ID': sessionId, Accept: 'text/event-stream' },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`stream ${response.status}`);
      }
      setConnected(true);
      backoff = RECONNECT_MIN_MS;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (!stopped) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Khung SSE ngăn nhau bằng một dòng trống. Cắt theo '\n\n' và GIỮ LẠI
        // phần đuôi: một khung có thể bị chia đôi giữa hai chunk mạng, và bỏ
        // phần đuôi là mất đúng những thông báo đến lúc đường truyền chậm.
        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';

        for (const frame of frames) {
          if (!frame.trim() || frame.startsWith(':')) continue; // nhịp tim
          const dataLine = frame
            .split('\n')
            .find((line) => line.startsWith('data: '));
          if (!dataLine) continue;
          try {
            const payload = JSON.parse(dataLine.slice(6)) as ProximityNotification;
            setLastNotification(payload);
            show(payload);
            callbackRef.current?.(payload);
          } catch {
            // Khung hỏng thì bỏ khung đó, không đóng cả kết nối.
          }
        }
      }
    }

    async function loop() {
      while (!stopped) {
        try {
          await readStream();
        } catch (error) {
          if (stopped || (error as Error)?.name === 'AbortError') return;
        }
        setConnected(false);
        if (stopped) return;
        // Server tự đóng kết nối sau 5 phút (xem geofence.STREAM_TTL_SECONDS),
        // nên rơi vào đây là chuyện BÌNH THƯỜNG, không phải lỗi.
        await new Promise<void>((resolve) => {
          timer = setTimeout(resolve, backoff);
        });
        backoff = Math.min(backoff * 2, RECONNECT_MAX_MS);
      }
    }

    void loop();
    return () => {
      stopped = true;
      controller.abort();
      if (timer) clearTimeout(timer);
      setConnected(false);
    };
  }, [apiBaseUrl, sessionId, enabled, show]);

  return { permission, connected, lastNotification, requestPermission };
}
