export type TelemetryEventType =
  | 'search'
  | 'location_ping'
  // POI đã được HIỂN THỊ trong kết quả tìm kiếm (metadata: request_id, query, rank)
  | 'poi_impression'
  // thời gian ở lại một POI đã click (dwell_ms) — trước đây bị gộp nhầm vào poi_impression
  | 'poi_dwell'
  | 'poi_click'
  | 'navigation_start'
  | 'review';

export type TelemetryEvent = {
  event_type: TelemetryEventType;
  poi_id?: string;
  query?: string;
  dwell_ms?: number;
  rating?: number;
  location?: {
    latitude: number;
    longitude: number;
    accuracy_meters?: number;
  };
  location_consent?: boolean;
  metadata?: Record<string, unknown>;
};

export type TelemetryState = {
  sessionId: string;
  queued: number;
  delivered: number;
  /** Số sự kiện bị vứt vì hàng đợi đầy — trước đây mất hoàn toàn im lặng. */
  dropped: number;
  transport: 'idle' | 'sending' | 'online' | 'fallback' | 'offline';
};

const STORAGE_KEY = 'nearby.session-id.v1';

class TelemetryClient {
  private apiBaseUrl: string;
  private queue: Array<TelemetryEvent & Record<string, unknown>> = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private delivered = 0;
  private transport: TelemetryState['transport'] = 'idle';
  private listeners = new Set<(state: TelemetryState) => void>();
  private cachedSessionId = '';
  private dropped = 0;
  private failedRounds = 0;
  private timerDelay = Infinity;

  constructor(apiBaseUrl: string) {
    this.apiBaseUrl = apiBaseUrl;
  }

  get sessionId() {
    if (this.cachedSessionId) return this.cachedSessionId;
    if (typeof window === 'undefined') return '';
    const existing = window.localStorage.getItem(STORAGE_KEY);
    if (existing) {
      this.cachedSessionId = existing;
      return existing;
    }
    this.cachedSessionId = window.crypto.randomUUID();
    window.localStorage.setItem(STORAGE_KEY, this.cachedSessionId);
    return this.cachedSessionId;
  }

  subscribe(listener: (state: TelemetryState) => void) {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => this.listeners.delete(listener);
  }

  capture(event: TelemetryEvent) {
    this.captureBatch([event]);
  }

  /** Nạp nhiều sự kiện bằng MỘT lần lên lịch gửi.
   *
   * Bắt buộc dùng cho lô impression: gọi capture() 50 lần liên tiếp sẽ đẩy
   * hàng đợi qua ngưỡng flush ở mỗi lần gọi, mà flush() lại tự chặn khi đang
   * có request bay, nên phần lớn sự kiện nằm lại chờ lần capture() kế tiếp.
   */
  captureBatch(events: TelemetryEvent[]) {
    const sessionId = this.sessionId;
    if (!sessionId || !events.length) return;
    for (const event of events) {
      this.queue.push({
        id: window.crypto.randomUUID(),
        session_id: sessionId,
        occurred_at: new Date().toISOString(),
        ...event,
      });
    }
    // Trần 100 cũ không chứa nổi hai lần tìm kiếm (51 sự kiện mỗi lần). Tệ hơn,
    // shift() vứt sự kiện CŨ NHẤT — mà 'search' và 'poi_click' luôn nằm TRƯỚC
    // burst impression, nên chính tín hiệu quý nhất bị vứt còn impression thì
    // giữ lại, làm CTR lệch xuống ở tử số. Nay vứt sự kiện MỚI NHẤT và chỉ vứt
    // impression, đồng thời đếm số đã mất thay vì mất im lặng.
    while (this.queue.length > 500) {
      let victim = this.queue.length - 1;
      for (let i = this.queue.length - 1; i >= 0; i -= 1) {
        if (this.queue[i].event_type === 'poi_impression') {
          victim = i;
          break;
        }
      }
      this.queue.splice(victim, 1);
      this.dropped += 1;
    }
    this.emit();
    // Lô lớn (impression sau tìm kiếm) gửi ngay; sự kiện lẻ vẫn gom 1,2 giây để
    // một cú click không thành hai request HTTP.
    if (this.queue.length >= 5) void this.flush();
    else this.scheduleFlush(1_200);
  }

  async flush() {
    if (!this.queue.length) return;
    // Đang có request bay: đặt hẹn giờ để phần còn lại được gửi tiếp. Trước đây
    // nhánh này return trắng, nên 46/51 sự kiện của một lô nằm kẹt vô thời hạn
    // cho tới lần capture() kế tiếp.
    if (this.transport === 'sending') {
      this.scheduleFlush(200);
      return;
    }
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    // Trần 50 khớp với EventBatch.max_length ở backend/app/models.py.
    const batch = this.queue.slice(0, 50);
    const batchIds = new Set(batch.map((item) => item.id as string));
    this.transport = 'sending';
    this.emit();
    try {
      const response = await fetch(`${this.apiBaseUrl}/api/v1/events/batch`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Session-ID': this.sessionId,
        },
        body: JSON.stringify({ events: batch }),
        keepalive: true,
      });
      if (!response.ok) {
        // PHẢI phân biệt lỗi vĩnh viễn với lỗi tạm thời.
        //
        // 4xx (trừ 429) nghĩa là backend sẽ KHÔNG BAO GIỜ nhận lô này: một sự
        // kiện sai định dạng — ví dụ query dài quá 160 ký tự, hay dwell_ms vượt
        // 24 giờ vì tab để mở qua đêm — làm Pydantic từ chối CẢ LÔ. Thử lại chỉ
        // gửi đúng lô đó mãi mãi: telemetry của cả phiên chết, và trình duyệt
        // bắn một request mỗi 5 giây suốt phần đời còn lại của tab.
        //
        // Nên với 4xx: vứt lô độc, đếm vào `dropped`, rồi đi tiếp. Mất 50 sự
        // kiện còn hơn mất tất cả sự kiện từ giây đó trở đi.
        const permanent = response.status >= 400 && response.status < 500 && response.status !== 429;
        if (permanent) {
          this.queue = this.queue.filter((item) => !batchIds.has(item.id as string));
          this.dropped += batch.length;
          this.transport = 'fallback';
          return;
        }
        throw new Error(`HTTP ${response.status}`);
      }
      const result = (await response.json()) as { delivery?: string };
      // Xóa theo DANH TÍNH, không theo vị trí: hàng đợi có thể đã bị cắt bớt
      // trong lúc request bay, khi đó splice(0, n) xóa nhầm sự kiện mới chưa gửi.
      this.queue = this.queue.filter((item) => !batchIds.has(item.id as string));
      this.delivered += batch.length;
      this.failedRounds = 0;
      this.transport = result.delivery === 'redis-stream' ? 'online' : 'fallback';
    } catch {
      this.transport = 'offline';
      this.failedRounds += 1;
    } finally {
      this.emit();
      if (this.queue.length) {
        // Backoff luỹ thừa có trần, chỉ áp dụng cho lỗi TẠM THỜI (mạng/5xx).
        const delay =
          this.transport === 'offline'
            ? Math.min(30_000, 2_000 * 2 ** Math.min(this.failedRounds - 1, 4))
            : 0;
        this.scheduleFlush(delay);
      }
    }
  }

  /** Gửi hết hàng đợi, không chỉ một lô 50. */
  async drain(maxRounds = 12) {
    for (let round = 0; round < maxRounds && this.queue.length; round += 1) {
      const before = this.queue.length;
      await this.flush();
      if (this.queue.length >= before) break; // không tiến triển thì dừng
    }
  }

  private scheduleFlush(delayMs: number) {
    // Cho phép DỜI SỚM một hẹn giờ đang chờ, nhưng không bao giờ dời MUỘN hơn:
    // nếu không, `if (this.timer) return` sẽ nuốt mất backoff 5 giây và biến
    // chế độ offline thành vòng thử lại mỗi 200 ms.
    if (this.timer !== null) {
      if (delayMs >= this.timerDelay) return;
      clearTimeout(this.timer);
    }
    this.timerDelay = delayMs;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.timerDelay = Infinity;
      void this.flush();
    }, delayMs);
  }

  snapshot(): TelemetryState {
    return {
      sessionId: this.sessionId,
      queued: this.queue.length,
      delivered: this.delivered,
      dropped: this.dropped,
      transport: this.transport,
    };
  }

  private emit() {
    const state = this.snapshot();
    this.listeners.forEach((listener) => listener(state));
  }
}

let singleton: TelemetryClient | null = null;

export function getTelemetry(apiBaseUrl: string) {
  if (!singleton) singleton = new TelemetryClient(apiBaseUrl);
  return singleton;
}
