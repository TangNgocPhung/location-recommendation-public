'use client';

import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  Bell,
  BellRing,
  Bike,
  Car,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  Copy,
  ExternalLink,
  Footprints,
  Globe,
  LoaderCircle,
  MapPin,
  MessageSquareText,
  Navigation,
  Phone,
  Route,
  Star,
  Tag,
  X,
} from 'lucide-react';

import { PoiCover } from '@/components/poi-cover';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

/* ------------------------------------------------------------------ *
 * Kiểu dữ liệu — gõ ĐÚNG hợp đồng API, không thêm không bớt tên trường.
 * ------------------------------------------------------------------ */

export type PoiEtaMinutes = { walk: number; motorbike: number; car: number };

export type PoiOpeningInterval = { opens: string; closes: string };

/** Backend trả thẳng cột `opening_hours` trong Postgres, mà cột đó có thể là
 *  `{}` (chưa có giờ), `{raw, alwaysOpen, periods}` (24/7) hoặc
 *  `{raw, parseStatus, periods}`. Vì vậy mọi trường đều optional — gõ bắt buộc
 *  ở đây sẽ khiến TypeScript nói dối về thứ thật sự đang chạy. */
export type PoiOpeningHours = {
  raw?: string | null;
  /** Ba giá trị backend thật sự gửi: 'parsed', 'missing' (OSM KHÔNG khai giờ —
   *  2.505/3.010 POI) và 'unsupported' (có khai nhưng bộ đọc chịu thua — 25
   *  POI). Hai cái sau khác hẳn nhau, đừng gộp. Không liệt kê 'missing' vào
   *  union bên dưới vì `| string` nuốt hết literal, thêm vào chỉ tổ đẻ thêm một
   *  lỗi typescript(no-redundant-type-constituents). */
  parseStatus?: 'parsed' | 'unsupported' | string | null;
  periods?: { days: number[]; opens: string; closes: string }[];
  alwaysOpen?: boolean;
};

export type PoiOpeningStatus = {
  openNow: boolean | null;
  closesInMinutes: number | null;
  opensInMinutes: number | null;
  /** Backend gửi kèm (app.opening_hours.opening_status): false nghĩa là chuỗi
   *  giờ mở cửa không đọc được, nên openNow=null là "không biết" chứ không phải
   *  "đóng". Panel không vẽ gì từ trường này, khai báo để hợp đồng khỏi lệch. */
  parsed?: boolean;
};

export type PoiWeekHour = {
  weekday: number;
  label: string;
  isToday: boolean;
  closed: boolean;
  unknown: boolean;
  intervals: PoiOpeningInterval[];
};

/** Khoá là chuỗi '1'..'5'. Để Partial vì chỉ cần backend quên một mức là cả
 *  biểu đồ vỡ; đọc bằng `?? 0` an toàn hơn tin vào hợp đồng. */
export type PoiRatingHistogram = Partial<
  Record<'1' | '2' | '3' | '4' | '5', number>
>;

export type PoiReviewSummary = {
  count: number;
  average: number | null;
  histogram: PoiRatingHistogram;
};

export type PoiReview = {
  id: string;
  authorName: string | null;
  rating: number;
  title: string | null;
  body: string;
  language: string;
  source: string;
  helpfulCount: number;
  createdAt: string;
};

export type PoiSimilar = {
  id: string;
  name: string;
  categoryLabel: string;
  address: string;
  rating: number | null;
  reviewCount: number;
  distanceMeters: number | null;
  latitude: number;
  longitude: number;
  reason: string;
};

export type PoiProvenance = {
  source: string;
  sourceId: string | null;
  updatedAt: string;
  h3: { r7: string; r8: string; r9: string };
  embeddingModel: string | null;
};

export type PoiDetail = {
  id: string;
  name: string;
  description: string;
  category: string;
  categoryLabel: string;
  address: string;
  district: string | null;
  city: string;
  countryCode: string;
  latitude: number;
  longitude: number;
  brand: string | null;
  website: string | null;
  phone: string | null;
  /** NULL = CHƯA AI ĐÁNH GIÁ. Không phải 0 điểm. 2982/3010 POI nhập từ
   *  OpenStreetMap rơi vào trường hợp này — đừng bao giờ hiện `rating ?? 0`. */
  rating: number | null;
  reviewCount: number;
  ratingSource: string | null;
  popularityScore: number;
  priceLevel: number;
  tags: string[];
  amenities: Record<string, unknown>;
  sponsored: boolean;
  timezone: string;
  openingHours: PoiOpeningHours;
  openingStatus: PoiOpeningStatus;
  weekHours: PoiWeekHour[];
  distanceMeters: number | null;
  etaMinutes: PoiEtaMinutes | null;
  popularityWindows: { w15: number; w1h: number; w24h: number };
  reviewSummary: PoiReviewSummary;
  reviews: PoiReview[];
  similar: PoiSimilar[];
  provenance: PoiProvenance;
};

export type PoiPhoto = {
  id: string;
  url: string;
  thumbUrl: string;
  width: number | null;
  height: number | null;
  title: string;
  /** 'place' = ảnh CỦA địa điểm (suy từ thẻ OSM image/wikimedia_commons/wikidata).
   *  'area'  = ảnh chụp quanh đó, tìm bằng geosearch — phần lớn là ảnh con phố,
   *  ảnh xe cộ. Nhãn trên giao diện KHÔNG được nhập nhằng hai loại này. */
  confidence: 'place' | 'area';
  distanceMeters: number | null;
  source: string;
  sourceUrl: string;
  license: string | null;
  attribution: string | null;
};

export type PoiPhotos = {
  poiId: string;
  status: 'ready' | 'empty' | 'unavailable';
  fetchedAt: string | null;
  photos: PoiPhoto[];
};

export type PoiRouteSummary = {
  poiId: string;
  durationMinutes: number;
  distanceMeters: number;
  // true khi tuyến được tính bằng đồ thị "car" thay cho "motorbike" (chưa có
  // hồ sơ xe máy thật, xem app/directions.py MODES) — panel phải nói rõ điều
  // này thay vì trình bày như tuyến xe máy đã đo đúng.
  approximate: boolean;
};

/* ------------------------------------------------------------------ *
 * Hàm định dạng
 * ------------------------------------------------------------------ */

function formatDistance(meters: number | null | undefined): string {
  if (meters == null || !Number.isFinite(meters)) return '—';
  if (meters < 1000) return `${Math.round(meters)} m`;
  // Dấu phẩy thập phân kiểu Việt Nam; toFixed luôn cho dấu chấm.
  return `${(meters / 1000).toFixed(1).replace('.', ',')} km`;
}

function formatMinutes(minutes: number | null | undefined): string {
  if (minutes == null || !Number.isFinite(minutes)) return '—';
  const value = Math.max(0, Math.round(minutes));
  if (value < 60) return `${value} phút`;
  const hours = Math.floor(value / 60);
  const rest = value % 60;
  return rest === 0 ? `${hours} giờ` : `${hours} giờ ${rest} phút`;
}

function normalizeWebsite(raw: string): string {
  // Thẻ `website` của OSM nhiều khi thiếu scheme ("quan-an.vn"). Gắn thẳng vào
  // href thì trình duyệt hiểu là đường dẫn tương đối và nhảy sang trang của
  // chính mình — người dùng tưởng web quán hỏng.
  return /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
}

function shortHost(raw: string): string {
  try {
    return new URL(normalizeWebsite(raw)).hostname.replace(/^www\./, '');
  } catch {
    return raw;
  }
}

function formatDate(iso: string, timezone: string): string {
  try {
    // Ghim timeZone: thành phần này vẫn được kết xuất một lần ở máy chủ rồi mới
    // hydrate ở trình duyệt. Để Intl tự lấy múi giờ máy thì hai lần cho hai
    // chuỗi khác nhau và React báo lệch DOM.
    return new Intl.DateTimeFormat('vi-VN', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      timeZone: timezone || 'Asia/Ho_Chi_Minh',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

const RATING_SOURCE_LABELS: Record<string, string> = {
  seed: 'dữ liệu mẫu nhập sẵn (seed)',
};

function ratingSourceLabel(raw: string | null | undefined): string {
  // In thẳng token thô ("seed") lên màn hình thì người đọc không biết con số kia
  // ở đâu ra. Token lạ vẫn giữ nguyên: thà khó hiểu còn hơn dịch bịa thành một
  // nguồn khác.
  if (!raw) return 'nguồn ngoài, chưa ghi rõ';
  return RATING_SOURCE_LABELS[raw] ?? raw;
}

const AMENITY_LABELS: Record<string, string> = {
  internet_access: 'Internet',
  takeaway: 'Mang đi',
  outdoor_seating: 'Chỗ ngồi ngoài trời',
  wheelchair: 'Lối cho xe lăn',
  delivery: 'Giao hàng',
  toilets: 'Nhà vệ sinh',
};

const AMENITY_VALUES: Record<string, string> = {
  yes: 'có',
  no: 'không',
  limited: 'hạn chế',
  only: 'chỉ hình thức này',
  wlan: 'Wi-Fi',
  wired: 'có dây',
  terminal: 'máy tính tại chỗ',
  balcony: 'ban công',
  sidewalk: 'vỉa hè',
  garden: 'sân vườn',
  rooftop: 'sân thượng',
  designated: 'có lối riêng',
};

function humanizeToken(token: string): string {
  return token.replace(/_/g, ' ');
}

type AmenityChip = { key: string; label: string; negative: boolean };

function toAmenityChips(
  amenities: Record<string, unknown> | null | undefined,
): AmenityChip[] {
  if (!amenities) return [];
  const chips: AmenityChip[] = [];
  for (const [key, rawValue] of Object.entries(amenities)) {
    if (rawValue == null) continue;
    const value = String(rawValue);
    const label = AMENITY_LABELS[key] ?? humanizeToken(key);
    if (value === 'yes') {
      chips.push({ key, label, negative: false });
      continue;
    }
    // Giữ nguyên giá trị lạ thay vì bỏ đi: thà hiện "toilets: 35" khó hiểu còn
    // hơn im lặng nuốt mất một dữ kiện có trong DB.
    const valueLabel = AMENITY_VALUES[value] ?? humanizeToken(value);
    chips.push({
      key,
      label: `${label}: ${valueLabel}`,
      negative: value === 'no',
    });
  }
  return chips;
}

/* ------------------------------------------------------------------ *
 * Mảnh giao diện dùng lại
 * ------------------------------------------------------------------ */

function StarRow({
  value,
  size = 'sm',
}: {
  value: number;
  size?: 'sm' | 'lg';
}) {
  const iconClass = size === 'lg' ? 'size-5' : 'size-3.5';
  return (
    <span className="flex items-center gap-px" aria-hidden>
      {[0, 1, 2, 3, 4].map((index) => {
        const fill = Math.min(Math.max(value - index, 0), 1);
        return (
          <span key={index} className="relative inline-flex">
            <Star className={cn(iconClass, 'text-amber-400/35')} />
            {fill > 0 && (
              <span
                className="absolute inset-y-0 left-0 overflow-hidden"
                style={{ width: `${fill * 100}%` }}
              >
                <Star
                  className={cn(iconClass, 'fill-amber-400 text-amber-400')}
                />
              </span>
            )}
          </span>
        );
      })}
    </span>
  );
}

function OpeningBadge({
  status,
}: {
  status: PoiOpeningStatus | null | undefined;
}) {
  // openNow === null nghĩa là KHÔNG BIẾT (không có giờ mở cửa, hoặc chuỗi
  // opening_hours không đọc được). Im lặng đúng hơn là đoán bừa "Đóng cửa".
  if (!status || status.openNow == null) return null;

  if (
    status.openNow &&
    status.closesInMinutes != null &&
    status.closesInMinutes <= 45
  ) {
    // Ngưỡng 45 phút lấy đúng theo thẻ kết quả trong location-explorer để hai
    // chỗ không nói ngược nhau về cùng một địa điểm.
    return (
      <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
        Sắp đóng · {status.closesInMinutes} phút
      </span>
    );
  }
  if (status.openNow) {
    return (
      <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300">
        Đang mở
      </span>
    );
  }
  return (
    <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
      {status.opensInMinutes != null && status.opensInMinutes <= 120
        ? `Mở sau ${status.opensInMinutes} phút`
        : 'Đóng cửa'}
    </span>
  );
}

function ActionButton({
  icon: Icon,
  label,
  ariaLabel,
  onClick,
  href,
  active,
}: {
  icon: typeof Bell;
  label: string;
  ariaLabel: string;
  onClick?: () => void;
  href?: string;
  active?: boolean;
}) {
  const circle = cn(
    'grid size-11 place-items-center rounded-full border transition-colors',
    active
      ? 'border-primary/40 bg-primary/10 text-primary'
      : 'border-emerald-950/15 bg-white text-primary hover:bg-emerald-50 dark:border-white/15 dark:bg-card dark:hover:bg-white/10',
  );
  const body = (
    <>
      <span className={circle}>
        <Icon className="size-5" />
      </span>
      <span className="max-w-[72px] truncate text-[11px] font-medium text-muted-foreground">
        {label}
      </span>
    </>
  );
  const wrapper =
    'flex w-16 shrink-0 flex-col items-center gap-1.5 rounded-lg py-1 outline-none focus-visible:ring-3 focus-visible:ring-ring/50';

  if (href) {
    // `normalizeWebsite` giữ nguyên scheme viết hoa ("HTTP://...") vì regex của
    // nó có cờ /i, trong khi chỗ này trước đây kiểm bằng startsWith('http') —
    // phân biệt hoa thường. Đúng những URL đó bị coi là nội bộ: nút mở đè lên
    // chính ứng dụng, người dùng mất khung bản đồ và panel đang đọc. Dùng lại y
    // nguyên regex trên để hai chỗ không còn cơ hội lệch nhau.
    const external = /^https?:\/\//i.test(href);
    return (
      <a
        href={href}
        aria-label={ariaLabel}
        target={external ? '_blank' : undefined}
        rel={external ? 'noopener noreferrer' : undefined}
        className={wrapper}
      >
        {body}
      </a>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      className={wrapper}
    >
      {body}
    </button>
  );
}

function InfoRow({
  icon: Icon,
  children,
}: {
  icon: typeof MapPin;
  children: ReactNode;
}) {
  return (
    <div className="flex gap-3 px-4 py-3">
      <Icon
        className="mt-0.5 size-4 shrink-0 text-muted-foreground"
        aria-hidden
      />
      <div className="min-w-0 flex-1 text-sm">{children}</div>
    </div>
  );
}

function PhotoCaption({
  photo,
  className,
}: {
  photo: PoiPhoto;
  className?: string;
}) {
  const isPlace = photo.confidence === 'place';
  return (
    <div className={cn('space-y-0.5 text-[11px] leading-snug', className)}>
      {/* data-tone là "móc" để chỗ gọi nhắm ĐÚNG dòng cảnh báo này. Nhắm theo
          thứ tự thẻ (<p> đầu tiên) thì tô nhầm cả nhánh isPlace vốn phải trung
          tính. */}
      <p
        data-tone={isPlace ? 'normal' : 'warn'}
        className={
          isPlace
            ? 'text-muted-foreground'
            : 'font-medium text-amber-700 dark:text-amber-400'
        }
      >
        {isPlace
          ? 'Ảnh của địa điểm · Wikimedia Commons'
          : `Ảnh khu vực${
              photo.distanceMeters != null
                ? ` · cách ${Math.round(photo.distanceMeters)} m`
                : ''
            } · không phải ảnh của địa điểm này`}
      </p>
      {/* Ghi công + giấy phép là ĐIỀU KIỆN của giấy phép CC, không phải phần
          trang trí được phép cắt cho gọn. */}
      <p className="text-muted-foreground">
        {photo.license ?? 'Giấy phép: chưa rõ'}
        {photo.attribution ? ` · ${photo.attribution}` : ''}
        {' · '}
        <a
          href={photo.sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-0.5 underline underline-offset-2 hover:text-foreground"
        >
          {photo.title}
          <ExternalLink className="size-3" aria-hidden />
        </a>
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Bảng chi tiết
 * ------------------------------------------------------------------ */

export function PoiDetailPanel(props: {
  detail: PoiDetail | null;
  photos: PoiPhotos | null;
  loading: boolean;
  error: string | null;
  /** tuyến OSRM tới POI này, do trang cha tính sẵn — hiển thị nếu poiId khớp */
  routeSummary: PoiRouteSummary | null;
  isGeofenced: boolean;
  apiBaseUrl: string;
  sessionId: string;
  onClose: () => void;
  onDirections: () => void;
  onToggleGeofence: () => void;
  onSelectSimilar: (poiId: string) => void;
  onReviewSubmitted: (summary: {
    ratingMean: number | null;
    ratingCount: number;
  }) => void;
}) {
  const {
    detail,
    photos,
    loading,
    error,
    routeSummary,
    isGeofenced,
    apiBaseUrl,
    sessionId,
    onClose,
    onDirections,
    onToggleGeofence,
    onSelectSimilar,
    onReviewSubmitted,
  } = props;

  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const [hoursOpen, setHoursOpen] = useState(false);
  const [copyState, setCopyState] = useState<'idle' | 'done' | 'failed'>(
    'idle',
  );
  const [scrolled, setScrolled] = useState(false);
  const [brokenIds, setBrokenIds] = useState<string[]>([]);
  const [reviewRating, setReviewRating] = useState(0);
  const [reviewAuthor, setReviewAuthor] = useState('');
  const [reviewTitle, setReviewTitle] = useState('');
  const [reviewBody, setReviewBody] = useState('');
  const [reviewState, setReviewState] = useState<
    'loading' | 'idle' | 'saving' | 'saved' | 'error'
  >('idle');
  const [reviewMessage, setReviewMessage] = useState('');
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lightboxRef = useRef<HTMLDivElement>(null);

  // Ảnh Commons thỉnh thoảng trả 404 (file bị đổi tên/xoá sau khi thẻ OSM được
  // ghi). Để nguyên thì trình duyệt vẽ icon ảnh vỡ ngay giữa thẻ chi tiết. Lọc
  // ra khỏi danh sách rồi rơi về ảnh bìa sinh sẵn thì trung thực hơn hẳn.
  const photoList = useMemo(
    () =>
      (photos?.photos ?? []).filter((photo) => !brokenIds.includes(photo.id)),
    [photos, brokenIds],
  );
  const poiId = detail?.id ?? null;

  // Ảnh trong lightbox có thể 404 ngay lúc người dùng đang xem: markBroken co
  // photoList lại còn lightboxIndex thì không, nên lightbox tự biến mất khỏi màn
  // hình mà state vẫn khác null — cú Esc đầu tiên bị nuốt cho một lớp không còn
  // ai nhìn thấy, và phím mũi tên vẫn lật một lightbox vô hình.
  // Kẹp lúc ĐỌC (không phải kẹp bằng useEffect rồi lưu lại): làm bằng effect thì
  // vừa thêm một lỗi react-compiler(EffectSetState), vừa hụt khi nhiều ảnh cùng
  // 404 trong một lượt gộp. Kẹp ở đây luôn khớp với độ dài THẬT của photoList,
  // và rơi về tấm liền trước nên người dùng thấy có chuyện xảy ra thay vì màn
  // hình đen tự biến mất. Mọi chỗ đọc chỉ số ảnh phải dùng `lightboxAt`, không
  // dùng `lightboxIndex` thô.
  const lightboxAt =
    lightboxIndex === null || photoList.length === 0
      ? null
      : Math.min(lightboxIndex, photoList.length - 1);
  // Cờ boolean chứ không dùng thẳng chỉ số: effect focus bên dưới mà phụ thuộc
  // vào chỉ số thì mỗi lần bấm "Ảnh kế tiếp" nó chạy lại và giật focus khỏi
  // chính nút mũi tên người dùng đang bấm.
  const lightboxOpen = lightboxAt !== null;

  // Đổi POI (bấm "địa điểm tương tự") mà không dọn các trạng thái cục bộ thì
  // lightbox còn mở với chỉ số ảnh của POI cũ, và bảng giờ vẫn bung của quán
  // trước — nhìn như dữ liệu bị trộn lẫn.
  useEffect(() => {
    setLightboxIndex(null);
    setHoursOpen(false);
    setCopyState('idle');
    setScrolled(false);
    setBrokenIds([]);
  }, [poiId]);

  // Một phiên chỉ có một đánh giá cho mỗi địa điểm. Khi mở lại panel, nạp đánh
  // giá cũ vào form để người dùng sửa thay vì vô tình tạo nhiều bản sao.
  useEffect(() => {
    if (!poiId || !sessionId) return;
    const controller = new AbortController();
    void (async () => {
      setReviewState('loading');
      try {
        const response = await fetch(
          `${apiBaseUrl}/api/v1/pois/${poiId}/reviews/me`,
          {
            headers: { 'X-Session-ID': sessionId },
            signal: controller.signal,
          },
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const { review } = (await response.json()) as {
          review: {
            rating: number;
            authorName: string | null;
            title: string | null;
            body: string;
          } | null;
        };
        if (controller.signal.aborted) return;
        if (review) {
          setReviewRating(review.rating);
          setReviewAuthor(review.authorName ?? '');
          setReviewTitle(review.title ?? '');
          setReviewBody(review.body ?? '');
        }
        setReviewState('idle');
      } catch (caught) {
        if ((caught as Error)?.name === 'AbortError') return;
        setReviewState('idle');
      }
    })();
    return () => controller.abort();
  }, [apiBaseUrl, poiId, sessionId]);

  useEffect(() => {
    return () => {
      if (copyTimer.current) clearTimeout(copyTimer.current);
    };
  }, []);

  // aria-modal="true" nói với trình đọc màn hình rằng phần còn lại của trang đã
  // biến mất. Để focus nằm lại nút ảnh bìa phía sau thì lời khai đó thành nói
  // dối: người dùng bàn phím Tab thẳng vào đúng chỗ ARIA vừa bảo là không có.
  useEffect(() => {
    if (!lightboxOpen) return;
    const opener = document.activeElement as HTMLElement | null;
    lightboxRef.current?.focus();
    return () => {
      // Nút đã mở có thể không còn (đổi POI, hoặc ảnh 404 bị markBroken lọc
      // mất). Gọi focus() lên node đã rời tài liệu là focus rơi thẳng về <body>.
      if (opener?.isConnected) opener.focus();
    };
  }, [lightboxOpen]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        // Esc phải đóng LỚP TRÊN CÙNG trước. Đóng cả panel khi người dùng chỉ
        // muốn thoát ảnh phóng to là mất luôn chỗ đang đọc. Xét `lightboxAt`
        // (đã kẹp) chứ không xét `lightboxIndex` thô: lightbox đã biến mất vì
        // ảnh 404 thì không được nuốt cú Esc của người dùng.
        if (lightboxAt !== null) {
          setLightboxIndex(null);
          return;
        }
        // Panel KHÔNG phải modal: cột trái vẫn gõ được khi panel mở, mà listener
        // lại nằm trên window nên nó nghe cả cú Esc gõ trong ô tìm kiếm. Gõ
        // tiếng Việt bằng Unikey/Telex hay bấm Esc để bỏ ô input là chuyện
        // thường ngày — để nguyên thì cú bấm đó đóng sập panel bên phải và
        // pushState làm bay luôn ?poi= khỏi thanh địa chỉ. Cố ý chắn Ở ĐÂY chứ
        // không chắn từ đầu hàm: lightbox đang mở thì Esc phải luôn thoát được
        // ảnh, kể cả khi focus còn nằm ở ô tìm kiếm.
        const target = event.target as HTMLElement | null;
        if (
          event.defaultPrevented ||
          event.isComposing ||
          target?.isContentEditable ||
          target?.tagName === 'INPUT' ||
          target?.tagName === 'TEXTAREA' ||
          target?.tagName === 'SELECT'
        ) {
          return;
        }
        onClose();
        return;
      }
      if (lightboxAt === null) return;
      if (event.key === 'Tab') {
        // aria-modal đã tuyên bố phần dưới không tồn tại; không bẫy Tab thì
        // vòng focus vẫn chạy thẳng vào đó, và ba nút của lightbox chỉ tới lượt
        // sau khi Tab hết cả panel.
        const root = lightboxRef.current;
        if (!root) return;
        const stops = root.querySelectorAll<HTMLElement>(
          'button:not([tabindex="-1"]), a[href]',
        );
        if (stops.length === 0) return;
        const first = stops[0];
        const last = stops[stops.length - 1];
        const active = document.activeElement;
        if (!root.contains(active) || (!event.shiftKey && active === last)) {
          event.preventDefault();
          first.focus();
        } else if (event.shiftKey && (active === first || active === root)) {
          event.preventDefault();
          last.focus();
        }
        return;
      }
      if (event.key === 'ArrowRight') {
        setLightboxIndex((lightboxAt + 1) % photoList.length);
      } else if (event.key === 'ArrowLeft') {
        setLightboxIndex(
          (lightboxAt - 1 + photoList.length) % photoList.length,
        );
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [lightboxAt, onClose, photoList.length]);

  const markBroken = useCallback((photoId: string) => {
    setBrokenIds((ids) => (ids.includes(photoId) ? ids : [...ids, photoId]));
  }, []);

  const handleShare = useCallback(() => {
    if (!detail) return;
    const share = () => {
      const url = new URL(window.location.href);
      url.searchParams.set('poi', detail.id);
      return url.toString();
    };
    let link = '';
    try {
      link = share();
    } catch {
      link = '';
    }
    if (copyTimer.current) clearTimeout(copyTimer.current);
    // clipboard chỉ tồn tại trong ngữ cảnh bảo mật (https hoặc localhost). Khi
    // mở qua IP LAN để demo trên điện thoại thì nó là undefined — phải nói thật
    // là không chép được chứ không hiện "Đã chép" rồi để người ta dán ra rỗng.
    const writer =
      link && navigator.clipboard?.writeText?.bind(navigator.clipboard);
    if (!writer) {
      setCopyState('failed');
      copyTimer.current = setTimeout(() => setCopyState('idle'), 2000);
      return;
    }
    writer(link)
      .then(() => setCopyState('done'))
      .catch(() => setCopyState('failed'))
      .finally(() => {
        copyTimer.current = setTimeout(() => setCopyState('idle'), 2000);
      });
  }, [detail]);

  async function handleReviewSubmit(event: { preventDefault(): void }) {
    event.preventDefault();
    if (!detail || !sessionId || reviewState === 'saving') return;
    if (reviewRating < 1 || reviewRating > 5) {
      setReviewState('error');
      setReviewMessage('Hãy chọn số sao trước khi gửi đánh giá.');
      return;
    }
    setReviewState('saving');
    setReviewMessage('');
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/pois/${detail.id}/reviews`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Session-ID': sessionId,
          },
          body: JSON.stringify({
            rating: reviewRating,
            author_name: reviewAuthor.trim() || null,
            title: reviewTitle.trim() || null,
            body: reviewBody.trim() || null,
          }),
        },
      );
      if (!response.ok) {
        let message = `Máy chủ trả lỗi ${response.status}`;
        try {
          const payload = (await response.json()) as { detail?: unknown };
          if (typeof payload.detail === 'string') message = payload.detail;
        } catch {
          // Giữ thông báo theo mã HTTP khi proxy trả về nội dung không phải JSON.
        }
        throw new Error(message);
      }
      const result = (await response.json()) as {
        updated: boolean;
        ratingMean: number | null;
        ratingCount: number;
      };
      setReviewState('saved');
      setReviewMessage(
        result.updated
          ? 'Đã cập nhật đánh giá của bạn.'
          : 'Cảm ơn bạn đã đánh giá!',
      );
      onReviewSubmitted(result);
    } catch (caught) {
      setReviewState('error');
      setReviewMessage(
        (caught as Error)?.message ||
          'Không gửi được đánh giá. Vui lòng thử lại.',
      );
    }
  }

  const header = (
    <div
      className={cn(
        'sticky top-0 z-20 flex items-center gap-2 px-3 py-2 transition-colors',
        scrolled
          ? 'border-b border-emerald-950/10 bg-white/90 backdrop-blur-xl dark:border-white/10 dark:bg-card/90'
          : 'bg-transparent',
      )}
    >
      <span
        className={cn(
          'min-w-0 flex-1 truncate text-sm font-semibold transition-opacity',
          scrolled ? 'opacity-100' : 'opacity-0',
        )}
      >
        {detail?.name ?? ''}
      </span>
      <button
        type="button"
        onClick={onClose}
        aria-label="Đóng bảng chi tiết địa điểm"
        className="grid size-8 shrink-0 place-items-center rounded-full bg-black/45 text-white backdrop-blur-sm transition-colors hover:bg-black/65 focus-visible:ring-3 focus-visible:ring-ring/50 data-[solid=true]:bg-muted data-[solid=true]:text-foreground data-[solid=true]:hover:bg-muted/70"
        data-solid={scrolled}
      >
        <X className="size-4" />
      </button>
    </div>
  );

  const shell = (children: ReactNode) => (
    <aside
      aria-label="Chi tiết địa điểm"
      className="flex size-full min-h-0 flex-col overflow-hidden bg-white text-foreground dark:bg-card"
    >
      <div
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
        onScroll={(event) => setScrolled(event.currentTarget.scrollTop > 160)}
      >
        {header}
        <div className="-mt-12">{children}</div>
      </div>
    </aside>
  );

  if (error) {
    return shell(
      <div className="px-4 pb-6 pt-16">
        <div className="rounded-2xl border border-destructive/30 bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">
            Không tải được chi tiết địa điểm
          </p>
          <p className="mt-1 text-muted-foreground">{error}</p>
        </div>
      </div>,
    );
  }

  if (!detail) {
    return shell(
      <div className="pb-6 pt-12">
        {loading ? (
          <>
            <Skeleton className="aspect-[16/10] w-full rounded-none" />
            <div className="space-y-3 px-4 pt-4">
              <Skeleton className="h-7 w-2/3" />
              <Skeleton className="h-4 w-1/2" />
              <div className="flex gap-3 pt-2">
                {[0, 1, 2, 3].map((index) => (
                  <Skeleton key={index} className="size-11 rounded-full" />
                ))}
              </div>
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
            </div>
          </>
        ) : (
          <p className="px-4 pt-6 text-sm text-muted-foreground">
            Chưa chọn địa điểm nào.
          </p>
        )}
      </div>,
    );
  }

  const cover = photoList[0] ?? null;
  const photosPending = loading && !photos;
  const today = detail.weekHours?.find((day) => day.isToday) ?? null;
  const amenityChips = toAmenityChips(detail.amenities);
  const histogram = detail.reviewSummary?.histogram ?? {};
  const histogramMax = Math.max(
    1,
    ...(['1', '2', '3', '4', '5'] as const).map((key) => histogram[key] ?? 0),
  );
  // KHÔNG rơi về `detail.rating`: đó là điểm tổng hợp sẵn của cột `pois.rating`,
  // khác nguồn với trung bình tính từ bảng `poi_reviews`. Trộn hai nguồn vào một
  // con số rồi đặt dưới biểu đồ histogram của poi_reviews là nói sai về dữ liệu.
  const averageRating = detail.reviewSummary?.average ?? null;
  const routeForThisPoi =
    routeSummary && routeSummary.poiId === detail.id ? routeSummary : null;

  // Cờ `unknown` của backend gộp hai chuyện khác hẳn nhau: OSM KHÔNG CÓ thẻ giờ
  // (parseStatus 'missing', 2.505/3.010 POI) và CÓ chuỗi nhưng bộ đọc chịu thua
  // ('unsupported', 25 POI). Nói "Chưa đọc được giờ" cho cả hai là tự nhận lỗi
  // thay cho dữ liệu thiếu, và giấu mất đúng 25 ca mà bộ đọc cần sửa thật.
  const unknownHoursText =
    detail.openingHours?.parseStatus === 'missing'
      ? 'Chưa có giờ mở cửa'
      : 'Chưa đọc được giờ';

  const todayText = detail.openingHours?.alwaysOpen
    ? 'Mở cả ngày (24/7)'
    : today == null || today.unknown
      ? unknownHoursText
      : today.closed
        ? 'Hôm nay đóng cửa'
        : (today.intervals ?? [])
            .map((slot) => `${slot.opens} – ${slot.closes}`)
            .join(', ');

  return (
    <>
      {shell(
        <>
          {/* a. ẢNH ------------------------------------------------------ */}
          <div className="relative">
            {photosPending ? (
              <Skeleton className="aspect-[16/10] w-full rounded-none" />
            ) : cover ? (
              <button
                type="button"
                onClick={() => setLightboxIndex(0)}
                aria-label={`Phóng to ảnh của ${detail.name}`}
                className="group relative block aspect-[16/10] w-full overflow-hidden bg-muted"
              >
                <img
                  src={cover.url}
                  alt={
                    cover.confidence === 'place'
                      ? `Ảnh của ${detail.name}`
                      : `Ảnh khu vực quanh ${detail.name}`
                  }
                  className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                  loading="lazy"
                  onError={() => markBroken(cover.id)}
                />
                {cover.confidence === 'area' && (
                  <span className="absolute left-3 top-14 rounded-full bg-amber-500/95 px-2 py-0.5 text-[11px] font-semibold text-white shadow-sm">
                    Ảnh khu vực
                  </span>
                )}
                {photoList.length > 1 && (
                  <span className="absolute bottom-3 right-3 rounded-full bg-black/60 px-2 py-0.5 text-[11px] font-medium text-white">
                    1/{photoList.length}
                  </span>
                )}
              </button>
            ) : (
              <div className="relative aspect-[16/10] w-full">
                <PoiCover name={detail.name} category={detail.category} />
                <p className="absolute inset-x-0 bottom-0 bg-black/45 px-3 py-1.5 text-[11px] leading-snug text-white/95">
                  {/* BA trạng thái khác hẳn nhau, gộp bất kỳ hai cái nào cũng là
                      nói sai về dữ liệu: "chưa hỏi nổi nguồn ảnh", "hỏi rồi,
                      nguồn thật sự không có ảnh", và "nguồn CÓ ảnh nhưng trình
                      duyệt tải không nổi" (mọi ảnh 404 nên brokenIds lọc sạch —
                      xem ghi chú ở photoList). Tới được đây thì photoList chắc
                      chắn rỗng, nên `photos.photos` còn phần tử tức là khác biệt
                      chỉ có thể do brokenIds. */}
                  {photos == null || photos.status === 'unavailable'
                    ? 'Chưa dò được ảnh — hệ thống chưa hỏi được nguồn ảnh, không phải là địa điểm không có ảnh'
                    : (photos.photos?.length ?? 0) > 0
                      ? `Không tải được ảnh — nguồn có ${photos.photos.length} ảnh nhưng trình duyệt tải không nổi (file trên Wikimedia Commons đã bị đổi tên/gỡ, hoặc mạng chặn). KHÔNG phải là địa điểm không có ảnh.`
                      : 'Chưa có ảnh cho địa điểm này'}
                </p>
              </div>
            )}
          </div>

          {cover && (
            <div className="space-y-2 px-4 pt-3">
              {photoList.length > 1 && (
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {photoList.map((photo, index) => (
                    <button
                      key={photo.id}
                      type="button"
                      onClick={() => setLightboxIndex(index)}
                      aria-label={`Xem ảnh ${index + 1} của ${detail.name}`}
                      className="relative size-16 shrink-0 overflow-hidden rounded-lg ring-1 ring-emerald-950/10 transition-transform hover:-translate-y-0.5 dark:ring-white/10"
                    >
                      <img
                        src={photo.thumbUrl}
                        alt=""
                        className="size-full object-cover"
                        loading="lazy"
                        onError={() => markBroken(photo.id)}
                      />
                      {photo.confidence === 'area' && (
                        <span
                          className="absolute inset-x-0 bottom-0 bg-amber-500/90 py-px text-center text-[9px] font-semibold text-white"
                          title="Ảnh chụp quanh khu vực, không phải ảnh của địa điểm"
                        >
                          khu vực
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
              <PhotoCaption photo={cover} />
            </div>
          )}

          {/* b. TÊN + XẾP HẠNG ------------------------------------------- */}
          <div className="px-4 pt-4">
            <div className="flex items-start gap-2">
              <h2 className="min-w-0 flex-1 text-2xl font-bold leading-tight">
                {detail.name}
              </h2>
              {detail.sponsored && (
                <Badge variant="secondary" className="mt-1 shrink-0">
                  Tài trợ
                </Badge>
              )}
            </div>

            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
              {detail.rating == null ? (
                <span className="text-muted-foreground">Chưa có đánh giá</span>
              ) : (
                <>
                  <span className="font-semibold text-amber-600 dark:text-amber-400">
                    {detail.rating.toFixed(1)}
                  </span>
                  <StarRow value={detail.rating} />
                  <span className="text-muted-foreground">
                    ({detail.reviewCount} đánh giá)
                  </span>
                </>
              )}
              <span className="text-muted-foreground">·</span>
              <span className="text-muted-foreground">
                {detail.categoryLabel}
              </span>
              {detail.priceLevel > 0 && (
                <>
                  <span className="text-muted-foreground">·</span>
                  <span
                    className="font-medium text-muted-foreground"
                    title={`Mức giá ${detail.priceLevel}/4`}
                  >
                    {'₫'.repeat(Math.min(detail.priceLevel, 4))}
                  </span>
                </>
              )}
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-2">
              <OpeningBadge status={detail.openingStatus} />
              {detail.brand && (
                <Badge variant="outline" className="bg-white dark:bg-card">
                  {detail.brand}
                </Badge>
              )}
            </div>

            {detail.description && (
              <p className="mt-2 text-sm text-muted-foreground">
                {detail.description}
              </p>
            )}
          </div>

          {/* c. HÀNG NÚT -------------------------------------------------- */}
          <div className="mt-4 flex gap-1 overflow-x-auto px-3 pb-1">
            <ActionButton
              icon={Navigation}
              label="Chỉ đường"
              ariaLabel={`Chỉ đường tới ${detail.name}`}
              onClick={onDirections}
            />
            <ActionButton
              icon={isGeofenced ? BellRing : Bell}
              label={isGeofenced ? 'Đang nhắc' : 'Nhắc tôi'}
              ariaLabel={
                isGeofenced
                  ? `Tắt nhắc khi tới gần ${detail.name}`
                  : `Nhắc tôi khi tới gần ${detail.name}`
              }
              active={isGeofenced}
              onClick={onToggleGeofence}
            />
            {detail.phone && (
              <ActionButton
                icon={Phone}
                label="Gọi"
                ariaLabel={`Gọi ${detail.name} số ${detail.phone}`}
                href={`tel:${detail.phone.replace(/\s+/g, '')}`}
              />
            )}
            {detail.website && (
              <ActionButton
                icon={Globe}
                label="Website"
                ariaLabel={`Mở website của ${detail.name}`}
                href={normalizeWebsite(detail.website)}
              />
            )}
            <ActionButton
              icon={copyState === 'done' ? Check : Copy}
              label={
                copyState === 'done'
                  ? 'Đã chép'
                  : copyState === 'failed'
                    ? 'Không chép được'
                    : 'Chia sẻ'
              }
              ariaLabel={`Chép liên kết tới ${detail.name}`}
              active={copyState === 'done'}
              onClick={handleShare}
            />
            <span className="sr-only" aria-live="polite">
              {copyState === 'done'
                ? 'Đã chép liên kết'
                : copyState === 'failed'
                  ? 'Không chép được liên kết'
                  : ''}
            </span>
          </div>

          <Separator className="mt-3" />

          {/* d. CÁC HÀNG THÔNG TIN ---------------------------------------- */}
          <div className="divide-y divide-border">
            <InfoRow icon={MapPin}>
              <p>{detail.address || 'Chưa có địa chỉ'}</p>
              <p className="text-xs text-muted-foreground">
                {[detail.district, detail.city].filter(Boolean).join(' · ')}
              </p>
            </InfoRow>

            <InfoRow icon={Clock}>
              {/* aria-label GHI ĐÈ text con, nên label tĩnh cũ ("Xem giờ mở cửa
                  cả tuần") nuốt luôn `todayText`: người dùng trình đọc màn hình
                  không bao giờ nghe được giờ hôm nay. Trạng thái thu/bung đã có
                  aria-expanded lo, không cần nhét chữ "Xem" vào label. */}
              <button
                type="button"
                onClick={() => setHoursOpen((open) => !open)}
                aria-expanded={hoursOpen}
                aria-label={`Giờ mở cửa hôm nay: ${todayText}. Mở bảng giờ cả tuần`}
                className="flex w-full items-center gap-2 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <span className="min-w-0 flex-1 truncate">{todayText}</span>
                <ChevronDown
                  className={cn(
                    'size-4 shrink-0 text-muted-foreground transition-transform',
                    hoursOpen && 'rotate-180',
                  )}
                  aria-hidden
                />
              </button>
              {hoursOpen && (
                <>
                  <table className="mt-2 w-full text-sm">
                    <tbody>
                      {(detail.weekHours ?? []).map((day) => (
                        <tr
                          key={day.weekday}
                          className={
                            day.isToday
                              ? 'font-semibold text-foreground'
                              : 'text-muted-foreground'
                          }
                        >
                          <td className="whitespace-nowrap py-1 pr-4 align-top">
                            {day.label}
                          </td>
                          <td className="py-1 align-top">
                            {day.unknown
                              ? unknownHoursText
                              : day.closed
                                ? 'Đóng cửa'
                                : (day.intervals ?? [])
                                    .map(
                                      (slot) =>
                                        `${slot.opens} – ${slot.closes}`,
                                    )
                                    .join(', ')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {/* Hiện chuỗi OSM gốc khi không đọc được: người bảo vệ đồ án
                      cần chỉ ra được là dữ liệu có, chỉ bộ đọc chưa hỗ trợ. */}
                  {detail.openingHours?.raw &&
                    (detail.weekHours ?? []).some((day) => day.unknown) && (
                      <p className="mt-2 rounded-lg bg-muted px-2 py-1 font-mono text-[11px] text-muted-foreground">
                        {detail.openingHours.raw}
                      </p>
                    )}
                </>
              )}
            </InfoRow>

            <InfoRow icon={Navigation}>
              {detail.distanceMeters != null && (
                <p className="font-medium">
                  {formatDistance(detail.distanceMeters)}
                </p>
              )}
              {detail.etaMinutes ? (
                <>
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      <Footprints className="size-3.5" aria-hidden />
                      {formatMinutes(detail.etaMinutes.walk)}
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <Bike className="size-3.5" aria-hidden />
                      {formatMinutes(detail.etaMinutes.motorbike)}
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <Car className="size-3.5" aria-hidden />
                      {formatMinutes(detail.etaMinutes.car)}
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    ước lượng theo đường chim bay
                  </p>
                </>
              ) : (
                <p className="mt-1 text-xs text-muted-foreground">
                  Chưa biết khoảng cách — cần vị trí của bạn
                </p>
              )}
              {routeForThisPoi && (
                <p className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300">
                  <Route className="size-3.5" aria-hidden />
                  {routeForThisPoi.approximate
                    ? 'Tuyến ô tô (xấp xỉ)'
                    : 'Theo đường thật'}
                  : {formatMinutes(routeForThisPoi.durationMinutes)} ·{' '}
                  {formatDistance(routeForThisPoi.distanceMeters)}
                </p>
              )}
            </InfoRow>

            {detail.phone && (
              <InfoRow icon={Phone}>
                <a
                  href={`tel:${detail.phone.replace(/\s+/g, '')}`}
                  className="hover:underline"
                >
                  {detail.phone}
                </a>
              </InfoRow>
            )}

            {detail.website && (
              <InfoRow icon={Globe}>
                <a
                  href={normalizeWebsite(detail.website)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                >
                  {shortHost(detail.website)}
                  <ExternalLink className="size-3" aria-hidden />
                </a>
              </InfoRow>
            )}

            {((detail.tags?.length ?? 0) > 0 || amenityChips.length > 0) && (
              <InfoRow icon={Tag}>
                <div className="flex flex-wrap gap-1.5">
                  {amenityChips.map((chip) => (
                    <span
                      key={chip.key}
                      className={cn(
                        'rounded-full px-2 py-0.5 text-xs font-medium',
                        chip.negative
                          ? 'bg-muted text-muted-foreground line-through'
                          : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
                      )}
                    >
                      {chip.label}
                    </span>
                  ))}
                  {/* Tag là token OSM thô ('noodles', 'korean'). Không dịch bịa
                      sang tiếng Việt — sai nghĩa còn tệ hơn để nguyên. */}
                  {(detail.tags ?? []).map((tag) => (
                    <span
                      key={tag}
                      className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                    >
                      {humanizeToken(tag)}
                    </span>
                  ))}
                </div>
              </InfoRow>
            )}
          </div>

          <Separator />

          {/* e. ĐÁNH GIÁ -------------------------------------------------- */}
          <section className="px-4 py-4">
            <h3 className="text-base font-semibold">Đánh giá</h3>
            <form
              onSubmit={handleReviewSubmit}
              className="mt-3 rounded-2xl border border-amber-200/80 bg-gradient-to-br from-amber-50 via-white to-orange-50 p-4 shadow-sm dark:border-amber-500/20 dark:from-amber-500/10 dark:via-card dark:to-orange-500/5"
            >
              <div className="flex items-start gap-3">
                <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-amber-400 text-amber-950 shadow-sm">
                  <MessageSquareText className="size-5" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">Trải nghiệm của bạn thế nào?</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Đánh giá của bạn giúp mọi người chọn địa điểm phù hợp hơn.
                  </p>
                </div>
              </div>

              <div className="mt-4">
                <fieldset className="flex items-center gap-1">
                  <legend className="sr-only">Chọn số sao</legend>
                  {[1, 2, 3, 4, 5].map((value) => {
                    const active = value <= reviewRating;
                    return (
                      <label
                        key={value}
                        className="group relative grid size-10 cursor-pointer place-items-center rounded-xl transition-all hover:-translate-y-0.5 hover:bg-amber-100 has-[:focus-visible]:ring-3 has-[:focus-visible]:ring-amber-400/40 dark:hover:bg-amber-500/15"
                      >
                        <input
                          type="radio"
                          name="rating"
                          value={value}
                          checked={reviewRating === value}
                          aria-label={`${value} sao`}
                          onChange={() => {
                            setReviewRating(value);
                            setReviewState('idle');
                            setReviewMessage('');
                          }}
                          className="sr-only"
                        />
                        <Star
                          className={cn(
                            'size-7 transition-all',
                            active
                              ? 'scale-110 fill-amber-400 text-amber-500'
                              : 'text-muted-foreground/35 group-hover:text-amber-400',
                          )}
                          aria-hidden
                        />
                      </label>
                    );
                  })}
                  <span className="ml-2 text-sm font-medium text-amber-700 dark:text-amber-300">
                    {reviewRating > 0
                      ? ['Rất tệ', 'Chưa tốt', 'Ổn', 'Rất tốt', 'Tuyệt vời'][
                          reviewRating - 1
                        ]
                      : 'Chọn số sao'}
                  </span>
                </fieldset>
              </div>

              <div className="mt-4 grid gap-3">
                <label className="grid gap-1.5 text-xs font-medium">
                  Tên hiển thị{' '}
                  <span className="font-normal text-muted-foreground">
                    (không bắt buộc)
                  </span>
                  <input
                    value={reviewAuthor}
                    onChange={(event) => setReviewAuthor(event.target.value)}
                    maxLength={80}
                    placeholder="Ví dụ: Minh Anh"
                    className="h-10 rounded-xl border border-input bg-white/80 px-3 text-sm font-normal outline-none transition-shadow placeholder:text-muted-foreground focus:ring-3 focus:ring-ring/25 dark:bg-background/70"
                  />
                </label>
                <label className="grid gap-1.5 text-xs font-medium">
                  Tiêu đề{' '}
                  <span className="font-normal text-muted-foreground">
                    (không bắt buộc)
                  </span>
                  <input
                    value={reviewTitle}
                    onChange={(event) => setReviewTitle(event.target.value)}
                    maxLength={160}
                    placeholder="Điều bạn ấn tượng nhất"
                    className="h-10 rounded-xl border border-input bg-white/80 px-3 text-sm font-normal outline-none transition-shadow placeholder:text-muted-foreground focus:ring-3 focus:ring-ring/25 dark:bg-background/70"
                  />
                </label>
                <label className="grid gap-1.5 text-xs font-medium">
                  Chia sẻ thêm{' '}
                  <span className="font-normal text-muted-foreground">
                    (không bắt buộc)
                  </span>
                  <textarea
                    value={reviewBody}
                    onChange={(event) => setReviewBody(event.target.value)}
                    maxLength={2000}
                    rows={3}
                    placeholder="Không gian, dịch vụ, món ăn, giá cả…"
                    className="resize-y rounded-xl border border-input bg-white/80 px-3 py-2.5 text-sm font-normal outline-none transition-shadow placeholder:text-muted-foreground focus:ring-3 focus:ring-ring/25 dark:bg-background/70"
                  />
                </label>
              </div>

              <div className="mt-3 flex items-center justify-between gap-3">
                <output
                  className={cn(
                    'min-w-0 text-xs',
                    reviewState === 'error'
                      ? 'text-destructive'
                      : reviewState === 'saved'
                        ? 'font-medium text-emerald-700 dark:text-emerald-300'
                        : 'text-muted-foreground',
                  )}
                >
                  {reviewState === 'loading'
                    ? 'Đang tải đánh giá của bạn…'
                    : reviewMessage}
                </output>
                <button
                  type="submit"
                  disabled={
                    !sessionId ||
                    reviewState === 'saving' ||
                    reviewState === 'loading'
                  }
                  className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-xl bg-amber-400 px-4 text-sm font-semibold text-amber-950 shadow-sm transition-all hover:-translate-y-0.5 hover:bg-amber-300 hover:shadow-md disabled:pointer-events-none disabled:opacity-50"
                >
                  {reviewState === 'saving' && (
                    <LoaderCircle className="size-4 animate-spin" aria-hidden />
                  )}
                  {reviewState === 'saving'
                    ? 'Đang gửi…'
                    : reviewRating > 0
                      ? 'Lưu đánh giá'
                      : 'Chọn sao để gửi'}
                </button>
              </div>
            </form>

            {/* Hai con số ở đây ĐO HAI THỨ KHÁC NHAU nên không hề mâu thuẫn —
                cái sai là dán cạnh nhau mà không khai nguồn. `reviewSummary.count`
                đếm bài viết thật trong bảng `poi_reviews` (hiện RỖNG hoàn toàn),
                còn `rating`/`reviewCount` ở đầu trang là số tổng hợp sẵn của cột
                `pois.rating` / `pois.review_count` (28 POI seed, từ 96 tới 4021
                lượt). Để nguyên câu "Chưa có đánh giá nào" thì đúng 28 POI đẹp
                nhất — nhóm chắc chắn được mở ra lúc bảo vệ — tự cãi lại dòng
                "(4021 đánh giá)" ngay trên cùng một màn hình. */}
            {(detail.reviewSummary?.count ?? 0) === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">
                {detail.reviewCount > 0
                  ? `Chưa có bài đánh giá nào viết trên Nearby. Con số ${detail.reviewCount} lượt đánh giá của địa điểm này là số tổng hợp sẵn lấy từ ${ratingSourceLabel(detail.ratingSource)}, không kèm nội dung từng bài.`
                  : 'Chưa có đánh giá nào. 99% địa điểm nhập từ OpenStreetMap không kèm đánh giá.'}
              </p>
            ) : (
              <>
                <div className="mt-3 flex items-center gap-5">
                  <div className="shrink-0 text-center">
                    <div className="text-4xl font-bold leading-none">
                      {averageRating != null ? averageRating.toFixed(1) : '—'}
                    </div>
                    {averageRating != null && (
                      <div className="mt-1.5 flex justify-center">
                        <StarRow value={averageRating} size="lg" />
                      </div>
                    )}
                    <div className="mt-1 text-xs text-muted-foreground">
                      {detail.reviewSummary.count} đánh giá
                    </div>
                  </div>
                  <div className="min-w-0 flex-1 space-y-1">
                    {(['5', '4', '3', '2', '1'] as const).map((key) => {
                      const count = histogram[key] ?? 0;
                      return (
                        <div
                          key={key}
                          className="flex items-center gap-2 text-xs"
                        >
                          <span className="w-3 text-right text-muted-foreground">
                            {key}
                          </span>
                          <span className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-muted">
                            <span
                              className="block h-full rounded-full bg-amber-400"
                              style={{
                                width: `${(count / histogramMax) * 100}%`,
                              }}
                            />
                          </span>
                          <span className="w-6 text-right text-muted-foreground">
                            {count}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>

                <ul className="mt-4 space-y-4">
                  {(detail.reviews ?? []).map((review) => (
                    <li
                      key={review.id}
                      className="border-t border-border pt-3 first:border-0 first:pt-0"
                    >
                      <div className="flex items-center gap-2">
                        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-emerald-50 text-xs font-bold text-primary dark:bg-emerald-500/15">
                          {(review.authorName ?? '?')
                            .trim()
                            .charAt(0)
                            .toLocaleUpperCase('vi-VN')}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium">
                            {review.authorName ?? 'Người dùng ẩn danh'}
                          </p>
                          <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                            <StarRow value={review.rating} />
                            <span>
                              {formatDate(review.createdAt, detail.timezone)}
                            </span>
                          </div>
                        </div>
                      </div>
                      {review.title && (
                        <p className="mt-2 text-sm font-medium">
                          {review.title}
                        </p>
                      )}
                      <p className="mt-1 text-sm text-muted-foreground">
                        {review.body}
                      </p>
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        Nguồn: {review.source}
                        {review.helpfulCount > 0
                          ? ` · ${review.helpfulCount} người thấy hữu ích`
                          : ''}
                      </p>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {detail.ratingSource && (
              <p className="mt-3 text-[11px] text-muted-foreground">
                Nguồn điểm đánh giá: {ratingSourceLabel(detail.ratingSource)}
              </p>
            )}
          </section>

          {/* f. ĐỊA ĐIỂM TƯƠNG TỰ ----------------------------------------- */}
          {(detail.similar?.length ?? 0) > 0 && (
            <>
              <Separator />
              <section className="px-4 py-4">
                <h3 className="text-base font-semibold">Địa điểm tương tự</h3>
                <div className="mt-2 grid gap-2">
                  {detail.similar.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => onSelectSimilar(item.id)}
                      aria-label={`Xem chi tiết ${item.name}`}
                      className="w-full rounded-xl border border-emerald-950/10 bg-white p-3 text-left transition-all hover:-translate-y-0.5 hover:shadow-md dark:border-white/10 dark:bg-card"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">
                            {item.name}
                          </p>
                          <p className="truncate text-xs text-muted-foreground">
                            {item.address}
                          </p>
                        </div>
                        <Badge
                          variant="outline"
                          className="shrink-0 bg-white dark:bg-card"
                        >
                          {item.categoryLabel}
                        </Badge>
                      </div>
                      <div className="mt-2 flex items-center gap-3 text-xs">
                        {item.rating == null ? (
                          <span className="text-muted-foreground">
                            Chưa có đánh giá
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 font-semibold text-amber-600 dark:text-amber-400">
                            <Star
                              className="size-3.5 fill-current"
                              aria-hidden
                            />
                            {item.rating.toFixed(1)}
                            <span className="font-normal text-muted-foreground">
                              ({item.reviewCount})
                            </span>
                          </span>
                        )}
                        <span className="ml-auto inline-flex items-center gap-1 font-medium text-primary">
                          <Navigation className="size-3.5" aria-hidden />
                          {formatDistance(item.distanceMeters)}
                        </span>
                      </div>
                      {item.reason && (
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          {item.reason}
                        </p>
                      )}
                    </button>
                  ))}
                </div>
              </section>
            </>
          )}

          {/* g. NGUỒN DỮ LIỆU --------------------------------------------- */}
          <Separator />
          <details className="group px-4 py-4 text-sm">
            <summary className="cursor-pointer list-none font-medium text-muted-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
              <span className="inline-flex items-center gap-1">
                <ChevronRight
                  className="size-4 transition-transform group-open:rotate-90"
                  aria-hidden
                />
                Nguồn dữ liệu
              </span>
            </summary>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-[11px] text-muted-foreground">
              <dt>source</dt>
              <dd className="break-all">{detail.provenance?.source ?? '—'}</dd>
              <dt>sourceId</dt>
              <dd className="break-all">
                {detail.provenance?.sourceId ?? '—'}
              </dd>
              <dt>toạ độ</dt>
              <dd>
                {detail.latitude.toFixed(6)}, {detail.longitude.toFixed(6)}
              </dd>
              <dt>H3 r7</dt>
              <dd className="break-all">{detail.provenance?.h3?.r7 ?? '—'}</dd>
              <dt>H3 r8</dt>
              <dd className="break-all">{detail.provenance?.h3?.r8 ?? '—'}</dd>
              <dt>H3 r9</dt>
              <dd className="break-all">{detail.provenance?.h3?.r9 ?? '—'}</dd>
              <dt>updatedAt</dt>
              <dd className="break-all">
                {detail.provenance?.updatedAt ?? '—'}
              </dd>
              <dt>embedding</dt>
              <dd className="break-all">
                {detail.provenance?.embeddingModel ?? 'chưa nhúng'}
              </dd>
              <dt>popularity</dt>
              <dd>
                {Number.isFinite(detail.popularityScore)
                  ? detail.popularityScore.toFixed(3)
                  : '—'}{' '}
                · 15p {detail.popularityWindows?.w15 ?? 0} · 1h{' '}
                {detail.popularityWindows?.w1h ?? 0} · 24h{' '}
                {detail.popularityWindows?.w24h ?? 0}
              </dd>
            </dl>
            {photos?.fetchedAt && (
              <p className="mt-2 text-[11px] text-muted-foreground">
                Ảnh dò lúc: {formatDate(photos.fetchedAt, detail.timezone)}
              </p>
            )}
          </details>
        </>,
      )}

      {/* Lightbox ------------------------------------------------------ */}
      {lightboxAt !== null && photoList[lightboxAt] && (
        <div
          ref={lightboxRef}
          tabIndex={-1}
          role="dialog"
          aria-modal="true"
          aria-label={`Ảnh của ${detail.name}`}
          className="fixed inset-0 z-[70] flex flex-col items-center justify-center bg-black/90 p-4 outline-none"
        >
          {/* Nền bấm-để-đóng phải là <button> thật, không phải onClick đặt trên
              chính lớp phủ: div mang handler chuột mà không có handler bàn phím
              đúng là thứ jsx-a11y chặn (correctness: error trong .oxlintrc.json).
              tabIndex={-1} để nó không chen vào vòng Tab của ba nút kia. */}
          <button
            type="button"
            tabIndex={-1}
            aria-hidden="true"
            onClick={() => setLightboxIndex(null)}
            className="absolute inset-0 cursor-default"
          />

          <button
            type="button"
            onClick={() => setLightboxIndex(null)}
            aria-label="Đóng ảnh phóng to"
            className="absolute right-4 top-4 grid size-10 place-items-center rounded-full bg-white/15 text-white hover:bg-white/25"
          >
            <X className="size-5" />
          </button>

          {photoList.length > 1 && (
            <>
              <button
                type="button"
                aria-label="Ảnh trước"
                onClick={() =>
                  setLightboxIndex(
                    (lightboxAt - 1 + photoList.length) % photoList.length,
                  )
                }
                className="absolute left-3 top-1/2 grid size-10 -translate-y-1/2 place-items-center rounded-full bg-white/15 text-white hover:bg-white/25"
              >
                <ChevronLeft className="size-5" />
              </button>
              <button
                type="button"
                aria-label="Ảnh kế tiếp"
                onClick={() =>
                  setLightboxIndex((lightboxAt + 1) % photoList.length)
                }
                className="absolute right-3 top-1/2 grid size-10 -translate-y-1/2 place-items-center rounded-full bg-white/15 text-white hover:bg-white/25"
              >
                <ChevronRight className="size-5" />
              </button>
            </>
          )}

          {/* `relative` để khối này nằm ĐÈ lên nút nền bấm-để-đóng ở trên (phần
              tử có position vẽ sau phần tử tĩnh): bấm vào chính tấm ảnh hay phần
              ghi công (có link ra Commons) không còn rơi trúng nền nữa, nên
              không cần stopPropagation — thứ vốn làm oxlint kêu vì đặt handler
              chuột lên một div tĩnh. */}
          <div className="relative flex max-h-full w-full max-w-3xl flex-col items-center gap-3">
            <img
              src={photoList[lightboxAt].url}
              alt={photoList[lightboxAt].title}
              className="max-h-[70vh] w-auto max-w-full rounded-lg object-contain"
              onError={() => markBroken(photoList[lightboxAt].id)}
            />
            <div className="w-full rounded-lg bg-white/10 p-3 text-white">
              {/* Nền lightbox luôn đen bất kể theme, nên dòng cảnh báo "ảnh khu
                  vực" phải là amber-400 ở CẢ hai theme. Không bỏ
                  [&_[data-tone=warn]] đi được: [&_p] là selector hậu duệ (0,1,1)
                  nên đè mất text-amber-700 đặt thẳng trên thẻ <p> (0,1,0) ở theme
                  sáng, trong khi biến thể dark (0,2,0) lại thoát được — cùng một
                  tấm ảnh mà hai theme nhấn mạnh khác nhau. */}
              <PhotoCaption
                photo={photoList[lightboxAt]}
                className="[&_a]:text-white [&_p]:text-white/85 [&_[data-tone=warn]]:text-amber-400"
              />
              <p className="mt-1 text-[11px] text-white/60">
                {lightboxAt + 1}/{photoList.length} · Esc để đóng
              </p>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
