'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import maplibregl, {
  type GeoJSONSource,
  type Map as MapLibreMap,
  type Marker,
} from 'maplibre-gl';
import {
  Activity,
  Bell,
  BellRing,
  Bike,
  CheckCircle2,
  Clock,
  Compass,
  DatabaseZap,
  Flame,
  Info,
  LocateFixed,
  MapPin,
  Moon,
  Navigation,
  Radio,
  Route,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Star,
  Sun,
  X,
} from 'lucide-react';

import { AboutDialog, useAboutDialog } from '@/components/about-dialog';
import { useProximityNotifications } from '@/hooks/use-proximity';
import { usePoiDetail } from '@/hooks/use-poi-detail';
import {
  PoiDetailPanel,
  type PoiRouteSummary,
} from '@/components/poi-detail-panel';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useTheme } from '@/hooks/use-theme';
import { getTelemetry, type TelemetryState } from '@/lib/telemetry';

type Poi = {
  id: string;
  name: string;
  description: string;
  category: string;
  categoryLabel: string;
  address: string;
  latitude: number;
  longitude: number;
  // Backend tra NULL khi chua ai danh gia (99% POI nhap tu OpenStreetMap).
  // NULL khac 0: "chua biet" khong phai "diem kem" — xem migration 0008.
  rating: number | null;
  reviewCount: number;
  popularityScore: number;
  distanceMeters?: number;
  reason?: string;
  openNow?: boolean | null;
  closesInMinutes?: number | null;
  opensInMinutes?: number | null;
  etaMinutes?: { walk: number; motorbike: number; car: number } | null;
  /** Thứ hạng 0-based do server gán SAU diversify. Optional vì POI mẫu,
   *  trending và recommendations dùng chung kiểu này và không có thứ hạng. */
  rank?: number;
  // Tín hiệu backend đã trả về từ trước nhưng chưa có gì hiển thị. Tất cả đều
  // optional vì type Poi dùng chung cho cả POI mẫu, /trending và /recommendations.
  weather?: WeatherInfo | null;
  weatherFactor?: number;
  traffic?: {
    factor: number;
    isPeakHour: boolean;
    densityPenalty: number;
    source: string;
  } | null;
  trendingScope?: 'hex' | 'global' | 'empty';
  liveNearbyUsers?: number;
  retrievalChannels?: string[];
};

type Position = { latitude: number; longitude: number };

type GeoFilterInfo =
  | {
      geoChannelMode: 'both' | 'h3' | 'geo_distance';
      h3Resolution?: number;
      h3RingK?: number;
      h3CellCount?: number;
      h3Origin?: string;
      h3Skipped?: string;
      h3Outline?: GeoJSON.Polygon | GeoJSON.MultiPolygon;
    }
  | { geoFilter: 'postgis' }
  | null;

type ContextualSearchResponse = {
  requestId: string;
  query: string;
  /** 'opensearch' = truy xuất đa kênh; 'postgis' = đã rơi về đường dự phòng. */
  retrievalBackend: 'opensearch' | 'postgis';
  geoFilter?: GeoFilterInfo;
  ranker?: string;
  searchCenter: Position & { source: 'parsed-location' | 'device-location' };
  parsedLocation: {
    matched: boolean;
    locationText?: string | null;
    bestMatch?: { canonicalName: string; confidence: number } | null;
  };
  results: Poi[];
};

type CategoryOption = {
  category: string;
  categoryLabel: string;
  count: number;
};
type TrendingQuery = { query: string; score: number };
type TrendingResponse = {
  redisConnected: boolean;
  pois: Poi[];
  queries: TrendingQuery[];
};
type RecommendationsResponse = {
  personalized: boolean;
  preferredCategories: string[];
  results: Poi[];
};

const DEFAULT_POSITION: Position = {
  latitude: 10.7757,
  longitude: 106.7009,
};
const MAX_USABLE_ACCURACY_METERS = 5_000;

const SAMPLE_POIS: Poi[] = [
  {
    id: 'poi-001',
    name: 'Cà phê Bến Nghé',
    description: 'Cà phê rang xay, không gian yên tĩnh để làm việc.',
    category: 'cafe',
    categoryLabel: 'Cà phê',
    address: '22 Lý Tự Trọng, Quận 1',
    latitude: 10.7784,
    longitude: 106.7018,
    rating: 4.7,
    reviewCount: 286,
    popularityScore: 0.91,
  },
  {
    id: 'poi-002',
    name: 'Phở Nhà Mình',
    description: 'Phở bò truyền thống, phục vụ từ sáng sớm.',
    category: 'restaurant',
    categoryLabel: 'Ăn uống',
    address: '38 Pasteur, Quận 1',
    latitude: 10.7748,
    longitude: 106.6996,
    rating: 4.6,
    reviewCount: 412,
    popularityScore: 0.95,
  },
  {
    id: 'poi-003',
    name: 'Bảo tàng Thành phố',
    description: 'Không gian lịch sử và kiến trúc giữa trung tâm Sài Gòn.',
    category: 'museum',
    categoryLabel: 'Văn hóa',
    address: '65 Lý Tự Trọng, Quận 1',
    latitude: 10.7763,
    longitude: 106.6994,
    rating: 4.5,
    reviewCount: 732,
    popularityScore: 0.88,
  },
  {
    id: 'poi-004',
    name: 'Vườn xanh Tao Đàn',
    description: 'Khoảng xanh rộng, phù hợp đi bộ và nghỉ trưa.',
    category: 'park',
    categoryLabel: 'Công viên',
    address: 'Trương Định, Quận 1',
    latitude: 10.7742,
    longitude: 106.6937,
    rating: 4.6,
    reviewCount: 968,
    popularityScore: 0.9,
  },
  {
    id: 'poi-005',
    name: 'Bếp Chợ Lớn',
    description: 'Món Việt hiện đại, phù hợp nhóm bạn và gia đình.',
    category: 'restaurant',
    categoryLabel: 'Ăn uống',
    address: '112 Nguyễn Huệ, Quận 1',
    latitude: 10.7735,
    longitude: 106.7045,
    rating: 4.4,
    reviewCount: 197,
    popularityScore: 0.79,
  },
  {
    id: 'poi-006',
    name: 'The Reading Room',
    description: 'Hiệu sách nhỏ kết hợp cà phê và khu đọc tại chỗ.',
    category: 'bookstore',
    categoryLabel: 'Mua sắm',
    address: '14 Đồng Khởi, Quận 1',
    latitude: 10.7769,
    longitude: 106.7059,
    rating: 4.8,
    reviewCount: 154,
    popularityScore: 0.86,
  },
];

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8081';
const SELECTED_POINT_COLOR = '#0f8a62';
// POI thật mang UUID từ database; POI mẫu hard-code mang id dạng 'poi-001'.
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Nền bản đồ. `demotiles` của MapLibre chỉ có đường biên quốc gia — marker POI
// nổi trên nền trắng trống, vô nghĩa với một ứng dụng tìm địa điểm đô thị.
// Mặc định dùng raster OpenStreetMap: không cần API key, có đường phố TP.HCM,
// và kèm sẵn attribution ODbL mà giấy phép share-alike bắt buộc phải hiển thị.
// Đặt VITE_MAP_STYLE_URL để chuyển sang style vector (MapTiler, Stadia, hoặc
// tileserver-gl tự dựng) khi cần chất lượng hiển thị cao hơn.
//
// Vì sao VITE_ chứ không phải NEXT_PUBLIC_: bản `vinext` (beta) dùng trong dự
// án này KHÔNG inline biến NEXT_PUBLIC_* vào bundle chạy trên trình duyệt ở
// chế độ dev — nó chỉ tồn tại phía server, nên `process.env.NEXT_PUBLIC_*`
// đọc ra undefined ngay trong `new maplibregl.Map(...)`. Vite thì thay
// `import.meta.env.VITE_*` bằng giá trị thật lúc transform, cho cả hai phía.
// Vẫn đọc NEXT_PUBLIC_ sau đó để không phá cấu hình cũ nếu framework sửa.
//
// KHÔNG gán cứng API key ở đây: repo này công khai trên GitHub, và một key
// nằm trong lịch sử git thì không xoá đi được nữa — phải revoke. Thiếu biến
// môi trường thì lùi về raster OpenStreetMap (không cần key), đúng như thiết
// kế ban đầu; bản đồ xấu hơn nhưng không ai phải lộ key để nó chạy.
//
// Chuỗi rỗng phải lùi về raster: compose luôn truyền biến này xuống (mặc định
// rỗng), mà `style: ''` làm MapLibre chết ngay lúc khởi tạo. Dùng `||` chứ
// không `??` vì `??` chỉ bắt undefined/null, không bắt chuỗi rỗng.
const VITE_ENV = (
  import.meta as unknown as {
    env?: Record<string, string | undefined>;
  }
).env;
const MAP_STYLE_URL =
  VITE_ENV?.VITE_MAP_STYLE_URL?.trim() ||
  process.env.NEXT_PUBLIC_MAP_STYLE_URL?.trim() ||
  '';
// `as const` trên version/type để TypeScript giữ literal 8 và 'raster' thay vì
// nới thành number/string — style spec của MapLibre yêu cầu đúng literal.
const OSM_RASTER_STYLE = {
  version: 8 as const,
  sources: {
    osm: {
      type: 'raster' as const,
      // KHÔNG dùng tile.openstreetmap.org: DNS ở Việt Nam (kiểm chứng trên máy
      // dev 14/09/2026) trả 127.0.0.1 / ::1 cho tên miền này, trong khi
      // Cloudflare DoH trả đúng IP Fastly — tức là chặn ở tầng phân giải tên,
      // không phải mạng hỏng. Hậu quả: mọi tile ERR_CONNECTION_REFUSED,
      // canvas MapLibre rỗng và lớp `.map-fallback` (nền CSS giả trong
      // globals.css) lộ ra. Người dùng đọc màn hình đó là "bản đồ vẽ xấu" chứ
      // không đoán được là bản đồ không tải nổi, nên đây là lỗi im lặng.
      //
      // tile.openstreetmap.de phục vụ cùng dữ liệu OSM, cùng cách render, không
      // cần key, và phân giải bình thường từ đây (200 OK, image/png). Vẫn giữ
      // raster ở nhánh dự phòng này: nó chỉ chạy khi KHÔNG có MAP_STYLE_URL, và
      // raster không cần glyphs/sprite nên ít thứ hỏng hơn khi mạng đã khó.
      tiles: ['https://tile.openstreetmap.de/{z}/{x}/{y}.png'],
      tileSize: 256,
      // Giữ 19: đã thử tay z18 và z19 trên máy chủ này, cả hai trả 200 PNG.
      maxzoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors (ODbL) &middot; tiles <a href="https://www.openstreetmap.de/">openstreetmap.de</a>',
    },
  },
  // Style raster không có sẵn font. Thiếu `glyphs`, lớp `cluster-count` (dùng
  // text-field để in số POI trong cụm) trượt kiểm tra style và bị MapLibre bỏ qua,
  // nên cụm hiện ra là vòng tròn xanh trống không có số. Font server demo của
  // MapLibre phục vụ sẵn Noto Sans Regular, không cần key.
  glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  layers: [{ id: 'osm', type: 'raster' as const, source: 'osm' }],
};
const DEFAULT_POINT_COLOR = '#f97316';
// Màu pin của POI đang chọn — đỏ quen mắt kiểu ghim Google Maps, tách hẳn khỏi
// bảng cam/xanh của các chấm POI để nhìn phát biết ngay "đây là chỗ vừa bấm".
const SELECTED_PIN_COLOR = '#ea4335';

type WeatherInfo = {
  isWet: boolean;
  isHeavyRain: boolean;
  temperatureC?: number | null;
  precipitationMm?: number | null;
  weatherCode?: number | null;
};

/** Câu mô tả thời tiết. Không bao giờ trả chuỗi rỗng — ô trống đọc như hỏng. */
function weatherLabel(w: WeatherInfo) {
  const temp =
    typeof w.temperatureC === 'number'
      ? ` ${Math.round(w.temperatureC)}°C`
      : '';
  if (w.isHeavyRain) return `Mưa to${temp}`;
  if (w.isWet) return `Đang mưa${temp}`;
  return `Trời khô${temp}`;
}

type RouteStep = {
  text: string;
  distanceMeters: number;
  durationSeconds: number;
  name: string | null;
};

type TransportMode = 'car' | 'motorbike' | 'foot';

type DirectionsResponse = {
  poiId: string;
  poiName: string;
  reason?: 'osrm-unavailable' | 'no-route';
  route: {
    geometry: GeoJSON.LineString;
    distanceMeters: number;
    durationMinutes: number;
    durationSeconds: number;
    steps: RouteStep[];
    cached: boolean;
    mode: TransportMode;
    approximate: boolean;
  } | null;
};

type RoutePlan = {
  poiId: string;
  poiName: string;
  geometry: GeoJSON.LineString;
  distanceMeters: number;
  durationMinutes: number;
  durationSeconds: number;
  steps: RouteStep[];
  cached: boolean;
  mode: TransportMode;
  approximate: boolean;
};

const TRANSPORT_MODES: { value: TransportMode; label: string; icon: string }[] =
  [
    { value: 'motorbike', label: 'Xe máy', icon: '🏍️' },
    { value: 'car', label: 'Ô tô', icon: '🚗' },
    { value: 'foot', label: 'Đi bộ', icon: '🚶' },
  ];

function distanceInMeters(from: Position, to: Position) {
  const radius = 6_371_000;
  const toRadians = (value: number) => (value * Math.PI) / 180;
  const latitudeDelta = toRadians(to.latitude - from.latitude);
  const longitudeDelta = toRadians(to.longitude - from.longitude);
  const fromLatitude = toRadians(from.latitude);
  const toLatitude = toRadians(to.latitude);
  const haversine =
    Math.sin(latitudeDelta / 2) ** 2 +
    Math.cos(fromLatitude) *
      Math.cos(toLatitude) *
      Math.sin(longitudeDelta / 2) ** 2;
  return (
    radius * 2 * Math.atan2(Math.sqrt(haversine), Math.sqrt(1 - haversine))
  );
}

function formatDistance(distance = 0) {
  return distance < 1_000
    ? `${Math.round(distance)} m`
    : `${(distance / 1_000).toFixed(1)} km`;
}

function enrichSamplePois(
  position: Position,
  query: string,
  radius: number,
  category: string | null,
) {
  const normalizedQuery = query.trim().toLocaleLowerCase('vi');
  return SAMPLE_POIS.map((poi) => ({
    ...poi,
    distanceMeters: distanceInMeters(position, {
      latitude: poi.latitude,
      longitude: poi.longitude,
    }),
  }))
    .filter((poi) => {
      const haystack =
        `${poi.name} ${poi.description} ${poi.categoryLabel}`.toLocaleLowerCase(
          'vi',
        );
      return (
        poi.distanceMeters <= radius &&
        (!normalizedQuery || haystack.includes(normalizedQuery)) &&
        (!category || poi.category === category)
      );
    })
    .sort((left, right) =>
      (left.distanceMeters ?? 0) === (right.distanceMeters ?? 0)
        ? (right.rating ?? -1) - (left.rating ?? -1)
        : (left.distanceMeters ?? 0) - (right.distanceMeters ?? 0),
    );
}

function poisToFeatureCollection(
  pois: Poi[],
): GeoJSON.FeatureCollection<GeoJSON.Point> {
  return {
    type: 'FeatureCollection',
    features: pois.map((poi) => ({
      type: 'Feature',
      properties: { id: poi.id },
      geometry: { type: 'Point', coordinates: [poi.longitude, poi.latitude] },
    })),
  };
}

function pointColorExpression(
  selectedPoiId: string | null,
): maplibregl.ExpressionSpecification {
  return [
    'case',
    ['==', ['get', 'id'], selectedPoiId ?? ''],
    SELECTED_POINT_COLOR,
    DEFAULT_POINT_COLOR,
  ];
}

// Mức zoom tối đa khi khung bản đồ theo tuyến đường — xem effect vẽ tuyến.
const ROUTE_MAX_ZOOM = 16;

function fitMapToResults(
  map: MapLibreMap | null,
  position: Position,
  results: Poi[],
) {
  if (!map || results.length === 0) return;
  const bounds = new maplibregl.LngLatBounds();
  bounds.extend([position.longitude, position.latitude]);
  for (const poi of results) {
    bounds.extend([poi.longitude, poi.latitude]);
  }
  map.fitBounds(bounds, { padding: 72, maxZoom: 15, duration: 600 });
}

// Mốc lọc theo số sao. 0 = không lọc. POI chưa có rating (null) bị loại khi
// bật lọc — không có dữ liệu thì không thể khẳng định nó đạt ngưỡng.
const MIN_RATING_OPTIONS = [0, 3, 3.5, 4, 4.5] as const;

function meetsMinRating(poi: Poi, minRating: number) {
  return minRating <= 0 || (poi.rating !== null && poi.rating >= minRating);
}

function chipClass(active: boolean) {
  return `whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
    active
      ? 'border-primary bg-primary text-primary-foreground'
      : 'border-emerald-950/10 bg-white text-muted-foreground hover:border-primary/40 hover:text-foreground dark:border-white/15 dark:bg-card'
  }`;
}

export function LocationExplorer() {
  const telemetry = useMemo(() => getTelemetry(API_BASE_URL), []);
  const { theme, toggleTheme } = useTheme();
  const about = useAboutDialog();
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const mapLoadedRef = useRef(false);
  const userMarkerRef = useRef<Marker | null>(null);
  // Pin đỏ đánh dấu POI đang chọn — kiểu ghim của Google Maps. Tách khỏi lớp
  // circle 'unclustered-point': lớp đó vẫn tô màu mọi POI, còn pin chỉ có MỘT
  // cái và luôn nổi trên cùng (Marker là overlay HTML, không bị layer che).
  const selectedMarkerRef = useRef<Marker | null>(null);
  const selectedSinceRef = useRef<number | null>(null);
  const poisRef = useRef<Poi[]>([]);
  const selectedPoiIdRef = useRef<string | null>(null);
  const focusPoiRef = useRef<(poi: Poi, source?: string) => void>(() => {});
  // Handler click marker được gắn MỘT LẦN trong effect khởi tạo bản đồ, nên nó
  // đóng băng mọi closure của lần render đầu. Đi qua ref là cách duy nhất để nó
  // gọi được bản openDetail mới nhất — y hệt focusPoiRef ngay trên.
  const openDetailRef = useRef<(poiId: string, source: string) => void>(
    () => {},
  );
  // POI cần bay tới NGAY KHI chi tiết về. Chỉ dùng cho đường vào không biết
  // trước toạ độ: mở bằng deep-link thì trong tay chỉ có mỗi UUID, mà để bản đồ
  // đứng yên ở Quận 1 trong khi panel nói về một quán ở Thủ Đức thì người nhận
  // link không hiểu mình đang xem cái gì.
  const flyToOnDetailRef = useRef<string | null>(null);
  // Ngữ cảnh lần tìm kiếm gần nhất. Click phải mang cùng request_id và rank với
  // impression, nếu không thì không ghép cặp được để tính CTR theo vị trí —
  // tức mất một nửa mục đích của việc ghi impression.
  const lastSearchRef = useRef<{
    requestId: string;
    ranks: Map<string, number>;
  } | null>(null);
  const [position, setPosition] = useState(DEFAULT_POSITION);
  // Bản sao cho các closure sống lâu (handler moveend của bản đồ, đăng ký một
  // lần lúc mount): đọc thẳng `position` ở đó là đóng băng vị trí mặc định.
  const positionRef = useRef<Position>(DEFAULT_POSITION);
  useEffect(() => {
    positionRef.current = position;
  }, [position]);
  // Vị trí đổi (người dùng vừa bật GPS, hoặc đang theo dõi liên tục) thì
  // khoảng cách của các POI đã nạp theo vùng cũng phải tính lại — không thì
  // thẻ vẫn khoe con số đo từ vị trí cũ cho tới lần kéo bản đồ kế tiếp.
  useEffect(() => {
    setAreaPois((current) =>
      current.map((poi) => ({
        ...poi,
        distanceMeters: distanceInMeters(position, {
          latitude: poi.latitude,
          longitude: poi.longitude,
        }),
      })),
    );
  }, [position]);
  const [query, setQuery] = useState('');
  const [radius, setRadius] = useState(3_000);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [minRating, setMinRating] = useState(0);
  const [categories, setCategories] = useState<CategoryOption[]>([]);
  const [trending, setTrending] = useState<TrendingResponse | null>(null);
  // Metadata CẤP TRUY VẤN: đường truy xuất đã chạy, vành H3 đã quét, thời tiết
  // tại tâm. Trước đây backend trả đủ nhưng không state nào giữ, nên toàn bộ
  // kiến trúc đa kênh vô hình với người dùng.
  const [searchMeta, setSearchMeta] = useState<{
    backend: 'opensearch' | 'postgis';
    geoFilter: GeoFilterInfo;
    ranker?: string;
  } | null>(null);
  const [recommendations, setRecommendations] = useState<Poi[]>([]);
  const [pois, setPois] = useState<Poi[]>(() =>
    enrichSamplePois(DEFAULT_POSITION, '', 3_000, null),
  );
  // POI trong vùng bản đồ đang nhìn — nạp lại mỗi khi kéo/zoom xong (moveend).
  // Tách khỏi `pois`: danh sách kết quả bên trái vẫn là của lần tìm kiếm, còn
  // các chấm trên bản đồ là hợp của cả hai — không có nó thì kéo bản đồ ra
  // khỏi vùng tìm kiếm là trống trơn dù DB có hàng chục nghìn POI.
  const [areaPois, setAreaPois] = useState<Poi[]>([]);
  const [selectedPoiId, setSelectedPoiId] = useState<string | null>('poi-001');
  // POI đang mở trong panel chi tiết. Cố tình TÁCH khỏi selectedPoiId: chọn một
  // POI (bấm thẻ trong danh sách, bấm marker) là thao tác nhẹ và xảy ra liên
  // tục khi lướt; mở panel là thao tác nặng, kéo theo hai yêu cầu mạng và che
  // mất nửa bản đồ. Gộp hai thứ làm một thì mỗi lần lướt danh sách là một lần
  // gọi Wikimedia.
  const [detailPoiId, setDetailPoiId] = useState<string | null>(null);
  const [status, setStatus] = useState('Dữ liệu mẫu tại trung tâm TP.HCM');
  const [isLoading, setIsLoading] = useState(false);
  const [hasLocationConsent, setHasLocationConsent] = useState(false);
  const [gpsStatus, setGpsStatus] = useState('Vị trí mặc định · chưa dùng GPS');
  // Theo dõi vị trí liên tục (lộ trình B13a). Trước đây chỉ có
  // getCurrentPosition gọi một lần mỗi khi bấm nút, nên topic
  // user-location-pings của sơ đồ gần như không bao giờ có dữ liệu — và cả
  // Dwell Time, Redis GEO theo phiên lẫn geofence đều đói theo.
  const [isWatching, setIsWatching] = useState(false);
  const [watchStatus, setWatchStatus] = useState('Theo dõi vị trí: tắt');
  const watchIdRef = useRef<number | null>(null);
  const lastPingRef = useRef<{ at: number; position: Position } | null>(null);
  // POI đã đăng ký "nhắc khi tới gần": poiId -> subscriptionId.
  const [geofences, setGeofences] = useState<Map<string, string>>(
    () => new Map(),
  );
  // Tuyến đường tới POI đang chọn, tính bằng OSRM tự dựng (lộ trình B16).
  // `null` phân biệt với `routeStatus` để giao diện nói được VÌ SAO chưa có
  // tuyến: đang tính, không có đường đi, hay chưa dựng dữ liệu định tuyến.
  const [route, setRoute] = useState<RoutePlan | null>(null);
  const [routeStatus, setRouteStatus] = useState<
    'idle' | 'loading' | 'none' | 'off'
  >('idle');
  // Mặc định "Xe máy" — phương tiện phổ biến nhất ở TP.HCM. Từ Phase 12.8 đã
  // có đồ thị riêng (osrm/motorbike.lua); máy nào chưa dựng thì backend lùi về
  // đồ thị ô tô và bật cờ `approximate`, giao diện đọc cờ đó chứ không đoán.
  const [transportMode, setTransportMode] =
    useState<TransportMode>('motorbike');
  const [showSteps, setShowSteps] = useState(false);
  const [parserStatus, setParserStatus] = useState(
    'Sẵn sàng hiểu “gần Bến Thành”',
  );
  const [gatewayStatus, setGatewayStatus] = useState('Chưa gửi yêu cầu');
  const [telemetryState, setTelemetryState] = useState<TelemetryState>({
    sessionId: '',
    queued: 0,
    delivered: 0,
    dropped: 0,
    transport: 'idle',
  });

  // Kênh thông báo tới gần. Chỉ mở khi người dùng đã đăng ký ít nhất một vùng
  // nhắc: giữ một kết nối SSE cho người chưa dùng tính năng này là tốn một
  // luồng của server để chờ một sự kiện không bao giờ tới.
  const proximity = useProximityNotifications({
    apiBaseUrl: API_BASE_URL,
    sessionId: telemetryState.sessionId,
    enabled: geofences.size > 0,
    onNotification: (notification) => {
      setStatus(
        `Bạn đang ở gần ${notification.title} · ${Math.round(notification.distanceMeters)} m`,
      );
    },
  });

  // clearWatch khi component rời đi. Thiếu đoạn này thì watchPosition sống lâu
  // hơn cả trang: GPS vẫn chạy, pin vẫn hao, và ping vẫn bắn từ một component
  // đã unmount.
  useEffect(() => {
    return () => {
      if (watchIdRef.current !== null) {
        navigator.geolocation.clearWatch(watchIdRef.current);
        watchIdRef.current = null;
      }
    };
  }, []);

  // Nạp lại vùng nhắc đã đăng ký khi có phiên: người dùng tải lại trang vẫn
  // phải thấy đúng những POI mình đã bật nhắc, nếu không nút sẽ báo sai trạng
  // thái và cú bấm kế tiếp tạo thêm một vùng trùng.
  useEffect(() => {
    const sessionId = telemetryState.sessionId;
    if (!sessionId) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/v1/geofences`, {
          headers: { 'X-Session-ID': sessionId },
        });
        if (!response.ok) return;
        const data = (await response.json()) as {
          subscriptions: Array<{ id: string; poiId: string | null }>;
        };
        if (cancelled) return;
        const next = new Map<string, string>();
        for (const item of data.subscriptions ?? []) {
          if (item.poiId) next.set(item.poiId, item.id);
        }
        setGeofences(next);
      } catch {
        // Không có vùng nhắc nào là trạng thái hợp lệ; im lặng là đúng ở đây.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [telemetryState.sessionId]);

  // Lọc rating chạy phía client trên kết quả đã có: đổi mốc sao không cần gọi
  // lại API, và danh sách lẫn bản đồ luôn khớp nhau.
  const ratedPois = useMemo(
    () => pois.filter((poi) => meetsMinRating(poi, minRating)),
    [pois, minRating],
  );

  // Kết quả tìm kiếm đứng trước và thắng khi trùng id: chúng mang request_id
  // và rank phục vụ telemetry, còn bản ghi từ /api/pois/nearby thì không.
  // Khai báo TRƯỚC effect lấy tuyến ngay dưới — deps của effect được đánh giá
  // lúc render, đặt sau là ReferenceError (temporal dead zone).
  const visiblePois = useMemo(() => {
    const seen = new Set(ratedPois.map((poi) => poi.id));
    return [
      ...ratedPois,
      ...areaPois.filter(
        (poi) => !seen.has(poi.id) && meetsMinRating(poi, minRating),
      ),
    ];
  }, [ratedPois, areaPois, minRating]);

  // POI đang chọn. Khai báo PHẢI nằm trên effect lấy tuyến bên dưới: mảng
  // dependency của effect đó đọc `selectedPoi?.id` NGAY TRONG LÚC RENDER, nên
  // để khai báo ở dưới là chạm vùng chết (TDZ) chứ không phải hoisting vô hại.
  //
  // `visiblePois` đổi ĐỊNH DANH mỗi lần areaPois nạp lại — chuyện bình thường
  // khi kéo bản đồ — nên effect lấy tuyến không được phụ thuộc vào chính mảng
  // đó, xem ghi chú ở effect.
  const selectedPoi = useMemo(
    () => visiblePois.find((item) => item.id === selectedPoiId) ?? null,
    [visiblePois, selectedPoiId],
  );

  // Lấy tuyến đường mỗi khi đổi POI đang chọn hoặc đổi vị trí người dùng.
  //
  // Huỷ bằng AbortController: chọn nhanh ba POI liên tiếp thì ba yêu cầu cùng
  // bay, và nếu không huỷ thì cái nào về SAU sẽ ghi đè — người dùng thấy tuyến
  // tới POI họ đã bỏ chọn. Đây là lỗi hay gặp và rất khó lần ra vì nó chỉ xảy
  // ra khi mạng chậm.
  useEffect(() => {
    // visiblePois chứ không chỉ pois: POI nạp theo vùng bản đồ (areaPois) cũng
    // chọn được từ marker, và thẻ của nó cũng phải có tuyến nội bộ — tra trong
    // mỗi kết quả tìm kiếm thì các POI đó vĩnh viễn không có đường đi.
    const poi = selectedPoi;
    if (!poi) {
      setRoute(null);
      setRouteStatus('idle');
      return;
    }

    const controller = new AbortController();
    setRouteStatus('loading');
    void (async () => {
      try {
        const params = new URLSearchParams({
          from_lat: String(position.latitude),
          from_lng: String(position.longitude),
          mode: transportMode,
        });
        if (UUID_PATTERN.test(poi.id)) {
          params.set('to_poi_id', poi.id);
        } else {
          // POI mẫu chưa tồn tại trong Postgres nên không có UUID. Gửi cặp toạ
          // độ của chính dữ liệu mẫu để backend vẫn tính OSRM và giữ người dùng
          // ở trong website.
          params.set('to_lat', String(poi.latitude));
          params.set('to_lng', String(poi.longitude));
          params.set('to_name', poi.name);
        }
        const response = await fetch(
          `${API_BASE_URL}/api/v1/directions?${params}`,
          {
            signal: controller.signal,
          },
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = (await response.json()) as DirectionsResponse;
        if (controller.signal.aborted) return;
        if (!data.route) {
          setRoute(null);
          setRouteStatus(data.reason === 'osrm-unavailable' ? 'off' : 'none');
          return;
        }
        setRoute({
          // Với POI mẫu backend trả id tổng quát vì nó chỉ nhận toạ độ. State
          // phía giao diện phải giữ id thật của thẻ để startNavigation ghép đúng
          // tuyến với địa điểm đang chọn.
          poiId: poi.id,
          poiName: poi.name,
          geometry: data.route.geometry,
          distanceMeters: data.route.distanceMeters,
          durationMinutes: data.route.durationMinutes,
          durationSeconds: data.route.durationSeconds,
          steps: data.route.steps ?? [],
          cached: Boolean(data.route.cached),
          mode: data.route.mode,
          approximate: data.route.approximate,
        });
        setRouteStatus('idle');
      } catch (error) {
        if ((error as Error)?.name === 'AbortError') return;
        setRoute(null);
        setRouteStatus('none');
      }
    })();

    return () => controller.abort();
    // CHỈ phụ thuộc giá trị nguyên thuỷ, không phụ thuộc object/mảng.
    //
    // Bản cũ liệt kê `visiblePois` và `position` và tạo ra một vòng lặp fetch
    // VÔ HẠN: setRoute trả object mới -> effect [route] gọi map.fitBounds ->
    // hoạt ảnh kết thúc bắn moveend -> loadAreaPois -> setAreaPois trả mảng
    // mới -> visiblePois đổi định danh -> effect này chạy lại -> setRoute...
    // Đo được 179 request /api/v1/directions và 160 request /api/pois/nearby
    // chỉ trong MỘT lượt xem trang, đủ để backend trả 429.
    //
    // Toạ độ và id là thứ thực sự quyết định tuyến đường; định danh của mảng
    // chứa chúng thì không.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    selectedPoi?.id,
    selectedPoi?.latitude,
    selectedPoi?.longitude,
    selectedPoi?.name,
    position.latitude,
    position.longitude,
    transportMode,
  ]);

  // Vẽ vành hexagon H3 của lần tìm kiếm gần nhất.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    const source = map.getSource('h3-ring') as GeoJSONSource | undefined;
    if (!source) return;
    const info = searchMeta?.geoFilter;
    const outline = info && !('geoFilter' in info) ? info.h3Outline : undefined;
    source.setData(
      outline
        ? {
            type: 'FeatureCollection',
            features: [{ type: 'Feature', properties: {}, geometry: outline }],
          }
        : { type: 'FeatureCollection', features: [] },
    );
  }, [searchMeta]);

  // Vẽ tuyến lên bản đồ và lùi khung nhìn cho vừa cả tuyến.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    const source = map.getSource('route') as GeoJSONSource | undefined;
    if (!source) return;

    if (!route) {
      source.setData({ type: 'FeatureCollection', features: [] });
      return;
    }
    source.setData({
      type: 'FeatureCollection',
      features: [{ type: 'Feature', properties: {}, geometry: route.geometry }],
    });

    const bounds = new maplibregl.LngLatBounds();
    for (const point of route.geometry.coordinates) {
      bounds.extend(point as [number, number]);
    }
    // padding phải to hơn bình thường: thẻ POI và bảng điều khiển che mất hai
    // góc bản đồ, nên tuyến vẽ sát mép sẽ nằm dưới lớp phủ.
    // maxZoom bắt buộc: POI đầu tiên được tự chọn có thể cách người dùng chỉ
    // vài mét (đo được 10 m ở Quận 11) — tuyến ngắn như vậy mà không kẹp zoom
    // thì fitBounds đẩy bản đồ tới mức sát nóc nhà, khung nhìn còn vài chục mét
    // và mọi POI khác rơi ra ngoài: người dùng thấy "quanh tôi không có gì".
    map.fitBounds(bounds, {
      padding: { top: 90, bottom: 190, left: 60, right: 330 },
      maxZoom: ROUTE_MAX_ZOOM,
      duration: 700,
    });
  }, [route]);

  // Thời tiết là tín hiệu CẤP TRUY VẤN: backend lấy một lần cho cả lượt tìm rồi
  // gắn cùng một object vào mọi ứng viên. Đọc từ kết quả đầu tiên là đủ.
  const queryWeather = useMemo<WeatherInfo | null>(
    () =>
      (pois.find((poi) => poi.weather)?.weather as WeatherInfo | undefined) ??
      null,
    [pois],
  );

  const h3Badge = useMemo(() => {
    const info = searchMeta?.geoFilter;
    if (!info || 'geoFilter' in info) return null;
    if (info.h3Skipped) return 'H3 bỏ qua · bán kính quá lớn';
    if (!info.h3CellCount) return null;
    return `H3 r${info.h3Resolution} · vành k=${info.h3RingK} · ${info.h3CellCount} ô`;
  }, [searchMeta]);

  const fallbackCategories = useMemo<CategoryOption[]>(() => {
    const counts = new Map<string, CategoryOption>();
    for (const poi of SAMPLE_POIS) {
      const existing = counts.get(poi.category);
      counts.set(poi.category, {
        category: poi.category,
        categoryLabel: poi.categoryLabel,
        count: (existing?.count ?? 0) + 1,
      });
    }
    return Array.from(counts.values());
  }, []);
  const categoryOptions =
    categories.length > 0 ? categories : fallbackCategories;
  // Trending/gợi ý phục vụ trạng thái khám phá ban đầu. Khi người dùng đã gõ
  // từ khoá hoặc chọn danh mục, đặt chúng trước kết quả sẽ đẩy đúng thứ họ vừa
  // tìm xuống dưới nếp gấp — đặc biệt rõ trên màn hình laptop có chiều cao CSS
  // thấp do display scaling.
  const showDiscovery = query.trim().length === 0 && selectedCategory === null;

  useEffect(() => {
    poisRef.current = visiblePois;
  }, [visiblePois]);
  useEffect(() => {
    selectedPoiIdRef.current = selectedPoiId;
  }, [selectedPoiId]);

  const focusPoi = useCallback(
    (poi: Poi, source = 'list') => {
      if (
        selectedPoiId &&
        selectedPoiId !== poi.id &&
        selectedSinceRef.current !== null
      ) {
        telemetry.capture({
          // Đây là sự kiện RỜI một POI đã click, không phải impression. Trước
          // đây nó mang nhãn 'poi_impression', khiến impression trở thành tập
          // con của click và đẩy CTR trong feature store tiến tới 1.0.
          event_type: 'poi_dwell',
          poi_id: selectedPoiId,
          dwell_ms: Date.now() - selectedSinceRef.current,
          metadata: { source },
        });
      }
      // Chỉ đóng dấu request_id khi POI này THỰC SỰ nằm trong lần tìm kiếm gần
      // nhất. lastSearchRef không bao giờ bị xoá, nên nếu gắn vô điều kiện thì
      // click vào POI từ trending hay gợi ý cũng mang request_id của một lần
      // tìm kiếm chẳng liên quan — tạo cặp impression/click giả trong bảng huấn
      // luyện, đúng loại nhiễu mà bước A2 sinh ra để loại bỏ.
      const searchContext = lastSearchRef.current;
      const rankInSearch = searchContext?.ranks.get(poi.id);
      telemetry.capture({
        event_type: 'poi_click',
        poi_id: poi.id,
        metadata: {
          source,
          ...(rankInSearch === undefined
            ? {}
            : { request_id: searchContext?.requestId, rank: rankInSearch }),
        },
      });
      selectedSinceRef.current = Date.now();
      setSelectedPoiId(poi.id);
      mapRef.current?.flyTo({
        center: [poi.longitude, poi.latitude],
        zoom: 15.5,
        essential: true,
      });
    },
    [selectedPoiId, telemetry],
  );
  useEffect(() => {
    focusPoiRef.current = focusPoi;
  }, [focusPoi]);

  // Ghi/xoá tham số ?poi= trên thanh địa chỉ. pushState chứ không đổi route:
  // đổi route sẽ dựng lại cả trang, bản đồ khởi tạo lại từ đầu và mất luôn
  // khung nhìn người dùng đang ở.
  const syncDetailUrl = useCallback((poiId: string | null, replace = false) => {
    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    if (poiId) url.searchParams.set('poi', poiId);
    else url.searchParams.delete('poi');
    const next = `${url.pathname}${url.search}${url.hash}`;
    if (replace) window.history.replaceState({ poi: poiId }, '', next);
    else window.history.pushState({ poi: poiId }, '', next);
  }, []);

  const openDetail = useCallback(
    (poiId: string, source: string) => {
      // POI mẫu hard-code không có trong database: backend trả 400 và panel sẽ
      // mở ra trống trơn. Nói thẳng nguyên nhân giống toggleGeofence thay vì
      // bày một khung rỗng để người dùng tự đoán.
      if (!UUID_PATTERN.test(poiId)) {
        setStatus(
          'Đây là địa điểm mẫu, chưa có trong dữ liệu thật — hãy tìm kiếm trước',
        );
        return;
      }
      setDetailPoiId(poiId);
      // Chỉ đẩy history khi THỰC SỰ đổi POI. Bấm lại đúng marker đang mở là
      // chuyện thường xuyên; pushState vô điều kiện thì mỗi cú bấm thêm một
      // mục vào lịch sử và người dùng phải bấm Lùi năm lần mới ra khỏi trang.
      if (detailPoiId !== poiId) syncDetailUrl(poiId);
      // CHỈ phát poi_click ở hai lối vào này. Mở từ marker hay từ thẻ "Đang
      // chọn" thì focusPoi đã phát rồi; phát thêm lần nữa là đếm một cú bấm
      // thành hai và thổi phồng CTR trong feature store — đúng lỗi mà nhãn
      // poi_dwell/poi_impression đã phải sửa một lần.
      if (source === 'deep-link' || source === 'similar') {
        telemetry.capture({
          event_type: 'poi_click',
          poi_id: poiId,
          metadata: { source: 'detail-panel' },
        });
      }
    },
    [detailPoiId, syncDetailUrl, telemetry],
  );
  useEffect(() => {
    openDetailRef.current = openDetail;
  }, [openDetail]);

  const closeDetail = useCallback(() => {
    setDetailPoiId(null);
    // Đóng panel phải HUỶ mục lịch sử mà openDetail đã đẩy, chứ không đẩy thêm
    // mục mới: đẩy thêm thì bấm Lùi rơi đúng vào mục ?poi= vừa rời, handler
    // popstate đọc lại id và bật panel lên — đúng cái bẫy mà comment ở handler
    // popstate tuyên bố đã tránh. Mỗi chu kỳ mở-đóng còn đẻ ra hai mục lịch sử.
    //
    // Chỉ lùi khi mục hiện tại DO CHÍNH TA đẩy (nhận ra qua history.state.poi).
    // Người vào thẳng bằng link chia sẻ ?poi= không có mục nào để lùi, gọi
    // back() là văng họ khỏi trang; trường hợp đó chỉ xoá tham số tại chỗ.
    if (typeof window !== 'undefined' && window.history.state?.poi)
      window.history.back();
    else syncDetailUrl(null, true);
  }, [syncDetailUrl]);

  // Chỉ đưa vị trí xuống hook khi người dùng ĐÃ cấp GPS. `position` khởi tạo
  // bằng DEFAULT_POSITION — một điểm hard-code ở trung tâm TP.HCM dùng để mồi
  // bản đồ, không phải chỗ người dùng đứng. Truyền nó đi thì panel in ra
  // "1,2 km · 15 phút đi bộ" đo từ một điểm không ai đứng, và nhánh trung thực
  // "Chưa biết khoảng cách — cần vị trí của bạn" trong panel thành mã chết.
  const {
    detail: poiDetail,
    photos: poiPhotos,
    loading: detailLoading,
    error: detailError,
    refreshDetail: refreshPoiDetail,
  } = usePoiDetail(
    API_BASE_URL,
    detailPoiId,
    hasLocationConsent ? position : null,
  );

  // Mở sẵn panel từ ?poi=<uuid> để link chia sẻ được. Chạy một lần lúc mount:
  // sau đó chính openDetail/closeDetail là nguồn sự thật của tham số này, đọc
  // lại URL nữa sẽ đá nhau.
  useEffect(() => {
    const shared = new URLSearchParams(window.location.search).get('poi');
    if (!shared || !UUID_PATTERN.test(shared)) return;
    setDetailPoiId(shared);
    flyToOnDetailRef.current = shared;
    telemetry.capture({
      event_type: 'poi_click',
      poi_id: shared,
      metadata: { source: 'detail-panel' },
    });
    // Mảng rỗng là CỐ Ý dù bên trong dùng telemetry: nó là useMemo([]) nên
    // không bao giờ đổi, còn cho nó vào deps thì mỗi lần đổi là thêm một
    // poi_click giả cho cùng một lần mở link.
  }, []);

  // Nút Lùi của trình duyệt. Thiếu popstate thì pushState ở trên biến nút Lùi
  // thành cái bẫy: URL lùi về nhưng panel vẫn mở, bấm tiếp là rời hẳn trang.
  useEffect(() => {
    const handlePopState = () => {
      const shared = new URLSearchParams(window.location.search).get('poi');
      setDetailPoiId(shared && UUID_PATTERN.test(shared) ? shared : null);
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    if (!poiDetail || flyToOnDetailRef.current !== poiDetail.id) return;
    flyToOnDetailRef.current = null;
    mapRef.current?.flyTo({
      center: [poiDetail.longitude, poiDetail.latitude],
      zoom: 15.5,
      essential: true,
    });
  }, [poiDetail]);

  // PoiDetail là tập cha của Poi về mặt trường dữ liệu, nên chuyển kiểu ở đây
  // không phải bịa thêm số nào — điều kiện bắt buộc để dùng lại startNavigation
  // và toggleGeofence vốn nhận Poi.
  const detailAsPoi = useMemo<Poi | null>(() => {
    if (!poiDetail) return null;
    return {
      id: poiDetail.id,
      name: poiDetail.name,
      description: poiDetail.description,
      category: poiDetail.category,
      categoryLabel: poiDetail.categoryLabel,
      address: poiDetail.address,
      latitude: poiDetail.latitude,
      longitude: poiDetail.longitude,
      rating: poiDetail.rating,
      reviewCount: poiDetail.reviewCount,
      popularityScore: poiDetail.popularityScore,
      distanceMeters: poiDetail.distanceMeters ?? undefined,
      etaMinutes: poiDetail.etaMinutes,
      openNow: poiDetail.openingStatus?.openNow ?? null,
      closesInMinutes: poiDetail.openingStatus?.closesInMinutes ?? null,
      opensInMinutes: poiDetail.openingStatus?.opensInMinutes ?? null,
    };
  }, [poiDetail]);

  // Tuyến OSRM chỉ được tính cho selectedPoiId. Mở panel cho một POI khác (deep
  // link, địa điểm tương tự) thì chưa có tuyến nào của nó — trả null để panel
  // im lặng, thay vì gán nhầm thời gian đi tới một quán khác.
  const detailRouteSummary = useMemo<PoiRouteSummary | null>(
    () =>
      route && detailPoiId && route.poiId === detailPoiId
        ? {
            poiId: route.poiId,
            durationMinutes: route.durationMinutes,
            distanceMeters: route.distanceMeters,
            approximate: route.approximate,
          }
        : null,
    [route, detailPoiId],
  );

  const spotlightPoi = useCallback(
    (poi: Poi, source: string) => {
      const enriched: Poi = {
        ...poi,
        distanceMeters: poi.distanceMeters ?? distanceInMeters(position, poi),
      };
      setPois((current) => {
        const exists = current.some((item) => item.id === enriched.id);
        return exists
          ? current.map((item) => (item.id === enriched.id ? enriched : item))
          : [enriched, ...current];
      });
      telemetry.capture({
        event_type: 'poi_click',
        poi_id: enriched.id,
        metadata: { source },
      });
      selectedSinceRef.current = Date.now();
      setSelectedPoiId(enriched.id);
      mapRef.current?.flyTo({
        center: [enriched.longitude, enriched.latitude],
        zoom: 15.5,
        essential: true,
      });
    },
    [position, telemetry],
  );

  useEffect(() => {
    const unsubscribe = telemetry.subscribe(setTelemetryState);
    // drain() thay vì flush(): flush chỉ tiêu thụ MỘT lô 50 sự kiện, mà hàng đợi
    // có thể tới 500 — đóng tab sẽ mất tới 450 sự kiện.
    const flush = () => void telemetry.drain();
    window.addEventListener('pagehide', flush);
    return () => {
      unsubscribe();
      window.removeEventListener('pagehide', flush);
    };
  }, [telemetry]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/v1/categories`);
        if (!response.ok) return;
        const data = (await response.json()) as CategoryOption[];
        if (!cancelled) setCategories(data);
      } catch {
        // API chưa sẵn sàng — dùng category suy ra từ dữ liệu mẫu.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/v1/trending?limit=6`);
        if (!response.ok) return;
        const data = (await response.json()) as TrendingResponse;
        if (!cancelled) setTrending(data);
      } catch {
        // Trending là tính năng nâng cao — im lặng bỏ qua khi backend chưa sẵn sàng.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!telemetryState.sessionId) return;
    let cancelled = false;
    void (async () => {
      try {
        const params = new URLSearchParams({
          lat: String(position.latitude),
          lng: String(position.longitude),
          session_id: telemetryState.sessionId,
          limit: '6',
        });
        const response = await fetch(
          `${API_BASE_URL}/api/v1/recommendations?${params}`,
        );
        if (!response.ok) return;
        const data = (await response.json()) as RecommendationsResponse;
        if (!cancelled) setRecommendations(data.results);
      } catch {
        // Cá nhân hóa là tính năng nâng cao — im lặng bỏ qua khi lỗi.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [telemetryState.sessionId, position.latitude, position.longitude]);

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: MAP_STYLE_URL || OSM_RASTER_STYLE,
      center: [DEFAULT_POSITION.longitude, DEFAULT_POSITION.latitude],
      zoom: 14,
      attributionControl: false,
    });
    map.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      'bottom-right',
    );
    map.addControl(
      new maplibregl.AttributionControl({ compact: true }),
      'bottom-left',
    );
    mapRef.current = map;

    // MapLibre chỉ phát `load` khi MỌI source trong style báo đã tải xong. Style
    // Streets của MapTiler có source `maptiler_attribution` kiểu vector nhưng không
    // url, không tiles — nó chỉ tồn tại để mang dòng ghi công — nên source đó không
    // bao giờ chuyển sang trạng thái loaded, `isStyleLoaded()` vĩnh viễn false và
    // `load` KHÔNG BAO GIỜ phát. Toàn bộ lớp POI, cụm và handler click nằm trong đây
    // nên bản đồ sẽ không có chấm nào, bấm cũng không ăn. `styledata` phát ngay khi
    // style được parse xong — đủ điều kiện để addSource/addLayer — và không phụ thuộc
    // vào việc tile tải được hay không.
    const initMapLayers = () => {
      map.addSource('pois', {
        type: 'geojson',
        data: poisToFeatureCollection(poisRef.current),
        cluster: true,
        clusterRadius: 50,
        clusterMaxZoom: 14,
      });

      // Vành hexagon H3 — vùng mà kênh 2 thật sự đã quét. Thêm ĐẦU TIÊN nên
      // nằm dưới cùng: nó là nền ngữ cảnh, không được che tuyến đường lẫn POI.
      map.addSource('h3-ring', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
      map.addLayer({
        id: 'h3-ring-fill',
        type: 'fill',
        source: 'h3-ring',
        paint: { 'fill-color': '#0ea5e9', 'fill-opacity': 0.07 },
      });
      map.addLayer({
        id: 'h3-ring-outline',
        type: 'line',
        source: 'h3-ring',
        paint: {
          'line-color': '#0ea5e9',
          'line-width': 1.5,
          'line-opacity': 0.55,
          'line-dasharray': [2, 2],
        },
      });

      // Tuyến đường thêm TRƯỚC các lớp POI để nó nằm DƯỚI marker — thứ tự
      // addLayer quyết định cái gì che cái gì trong MapLibre, và một đường kẻ
      // dày 6 px vẽ đè lên chấm POI sẽ che mất đúng cái đích đến.
      map.addSource('route', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
      // Hai lớp chồng nhau: một viền trắng dày ở dưới, một nét màu mảnh ở trên.
      // Đường đơn sắc chìm nghỉm trên nền bản đồ nhiều màu của MapTiler.
      map.addLayer({
        id: 'route-casing',
        type: 'line',
        source: 'route',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#ffffff',
          'line-width': 9,
          'line-opacity': 0.9,
        },
      });
      map.addLayer({
        id: 'route-line',
        type: 'line',
        source: 'route',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': SELECTED_POINT_COLOR, 'line-width': 5 },
      });

      map.addLayer({
        id: 'clusters',
        type: 'circle',
        source: 'pois',
        filter: ['has', 'point_count'],
        paint: {
          'circle-color': [
            'step',
            ['get', 'point_count'],
            '#6ee7b7',
            10,
            '#34d399',
            30,
            '#059669',
          ],
          'circle-radius': ['step', ['get', 'point_count'], 16, 10, 20, 30, 26],
          'circle-stroke-width': 3,
          'circle-stroke-color': '#ffffff',
        },
      });
      map.addLayer({
        id: 'cluster-count',
        type: 'symbol',
        source: 'pois',
        filter: ['has', 'point_count'],
        layout: {
          'text-field': ['get', 'point_count_abbreviated'],
          // Mặc định của MapLibre là Open Sans Regular — font server demo không có
          // font đó (404). Noto Sans Regular có ở cả demo lẫn MapTiler.
          'text-font': ['Noto Sans Regular'],
          'text-size': 12,
        },
        paint: { 'text-color': '#ffffff' },
      });
      map.addLayer({
        id: 'unclustered-point',
        type: 'circle',
        source: 'pois',
        filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-radius': 9,
          'circle-stroke-width': 3,
          'circle-stroke-color': '#ffffff',
          'circle-color': pointColorExpression(selectedPoiIdRef.current),
        },
      });

      map.on('click', 'clusters', (event) => {
        const features = map.queryRenderedFeatures(event.point, {
          layers: ['clusters'],
        });
        const clusterId = features[0]?.properties?.cluster_id as
          | number
          | undefined;
        const source = map.getSource('pois') as GeoJSONSource | undefined;
        if (clusterId === undefined || !source) return;
        void source
          .getClusterExpansionZoom(clusterId)
          .then((zoom) => {
            const geometry = features[0].geometry as GeoJSON.Point;
            map.easeTo({
              center: geometry.coordinates as [number, number],
              zoom,
            });
          })
          .catch(() => undefined);
      });

      map.on('click', 'unclustered-point', (event) => {
        const feature = event.features?.[0];
        const poiId = feature?.properties?.id as string | undefined;
        const poi = poisRef.current.find((item) => item.id === poiId);
        if (!poi) return;
        // CHỈ chọn (thẻ "Đang chọn" + tuyến đường), KHÔNG mở panel chi tiết:
        // panel che gần nửa bản đồ và kéo theo hai yêu cầu mạng, bung nó ra
        // theo mỗi cú bấm marker là quá tay. Muốn xem chi tiết thì bấm "Xem
        // chi tiết" trên thẻ, hoặc bấm lần nữa vào ghim đỏ đang chọn.
        focusPoiRef.current(poi, 'map');
      });

      for (const layer of ['clusters', 'unclustered-point']) {
        map.on('mouseenter', layer, () => {
          map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', layer, () => {
          map.getCanvas().style.cursor = '';
        });
      }

      mapLoadedRef.current = true;
      loadAreaPois();
    };

    // Nạp POI cho vùng đang nhìn. Bán kính lấy ~nửa đường chéo viewport (tâm →
    // góc đông bắc) nên zoom xa thì quét rộng, zoom gần thì quét hẹp; kẹp theo
    // giới hạn của API (100..50000 m). Chỉ đổ vào areaPois — không đụng danh
    // sách kết quả tìm kiếm.
    const loadAreaPois = () => {
      const center = map.getCenter();
      const radiusMeters = Math.min(
        50_000,
        Math.max(
          300,
          Math.round(center.distanceTo(map.getBounds().getNorthEast())),
        ),
      );
      const params = new URLSearchParams({
        lat: center.lat.toFixed(6),
        lng: center.lng.toFixed(6),
        radius: String(radiusMeters),
        limit: '100',
      });
      fetch(`${API_BASE_URL}/api/pois/nearby?${params}`)
        .then((response) =>
          response.ok ? (response.json() as Promise<Poi[]>) : Promise.reject(),
        )
        // distanceMeters của API tính từ TÂM BẢN ĐỒ (tham số lat/lng ở trên) —
        // hiển thị lên thẻ sẽ thành "cách 200 m" trong khi người dùng đứng cách
        // 5 km. Tính lại từ vị trí thiết bị; đọc qua ref vì closure này đăng ký
        // một lần lúc mount, còn vị trí thì đổi khi người dùng bật GPS.
        .then((data) =>
          setAreaPois(
            data.map((poi) => ({
              ...poi,
              distanceMeters: distanceInMeters(positionRef.current, {
                latitude: poi.latitude,
                longitude: poi.longitude,
              }),
            })),
          ),
        )
        // Backend chưa chạy thì thôi — bản đồ vẫn còn chấm của lần tìm kiếm.
        .catch(() => undefined);
    };
    // Debounce: moveend bắn cả khi easeTo/flyTo kết thúc và khi người dùng thả
    // tay giữa chuỗi thao tác kéo — không gõ backend theo từng cú hích.
    let moveTimer: ReturnType<typeof setTimeout> | undefined;
    const handleMoveEnd = () => {
      clearTimeout(moveTimer);
      moveTimer = setTimeout(loadAreaPois, 350);
    };
    map.on('moveend', handleMoveEnd);

    if (map.isStyleLoaded()) initMapLayers();
    else void map.once('styledata', initMapLayers);

    return () => {
      clearTimeout(moveTimer);
      map.remove();
      mapRef.current = null;
      mapLoadedRef.current = false;
    };
  }, []);

  // Panel chi tiết là z-30 và phủ trọn mép phải, trong khi cặp nút +/- của
  // MapLibre chỉ có z-index:2 — mà không lớp nào ở giữa tạo stacking context,
  // nên panel nuốt gọn hai nút: mở panel ra là chỉ còn lăn chuột/pinch mới phóng
  // to được. Đẩy góc control sang trái đúng bề rộng panel chứ KHÔNG nâng
  // z-index: nâng z-index thì hai nút lại nổi lên TRÊN MẶT panel và che nội dung
  // bên trong. Ba góc còn lại đều đã có người ở nên cũng không dời góc được.
  //
  // Ghi thẳng style vào DOM chứ không viết rule trong globals.css: maplibre-gl.css
  // nạp KHÔNG layer nên luôn thắng utility Tailwind (nằm trong @layer utilities) —
  // đúng cái bẫy đã ghi ở comment của .map-canvas-host trong globals.css.
  useEffect(() => {
    const corner = mapContainerRef.current?.querySelector<HTMLElement>(
      '.maplibregl-ctrl-bottom-right',
    );
    if (!corner) return;
    if (!detailPoiId) {
      corner.style.right = '';
      return;
    }
    // Phải bám ĐÚNG breakpoint sm: của panel (w-[92%] -> sm:w-[420px]). Lệch số
    // ở đây là nút zoom chui lại xuống dưới panel mà không ai thấy.
    const wide = window.matchMedia('(min-width: 40rem)');
    const apply = () => {
      corner.style.right = wide.matches ? '420px' : '92%';
    };
    apply();
    wide.addEventListener('change', apply);
    return () => {
      wide.removeEventListener('change', apply);
      corner.style.right = '';
    };
  }, [detailPoiId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    const source = map.getSource('pois') as GeoJSONSource | undefined;
    if (!source) return;
    source.setData(poisToFeatureCollection(visiblePois));
    map.setPaintProperty(
      'unclustered-point',
      'circle-color',
      pointColorExpression(selectedPoiId),
    );
  }, [visiblePois, selectedPoiId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    userMarkerRef.current?.remove();
    const userDot = document.createElement('div');
    userDot.className =
      'h-5 w-5 rounded-full border-[3px] border-white bg-sky-500 shadow-[0_0_0_5px_rgb(14_165_233/22%)]';
    userDot.setAttribute('aria-label', 'Vị trí của bạn');
    userMarkerRef.current = new maplibregl.Marker({ element: userDot })
      .setLngLat([position.longitude, position.latitude])
      .addTo(map);
  }, [position]);

  // Ghim pin đỏ lên POI đang chọn. Tạo mới thay vì setLngLat trên marker cũ:
  // bỏ chọn (selectedPoi = null) thì pin phải BIẾN MẤT, mà một marker sống dai
  // không có API "ẩn" — remove rồi tạo lại là đường đơn giản và đủ rẻ vì thao
  // tác chọn không xảy ra hàng chục lần mỗi giây.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    selectedMarkerRef.current?.remove();
    selectedMarkerRef.current = null;
    if (!selectedPoi) return;
    const marker = new maplibregl.Marker({ color: SELECTED_PIN_COLOR })
      .setLngLat([selectedPoi.longitude, selectedPoi.latitude])
      .addTo(map);
    const element = marker.getElement();
    element.style.cursor = 'pointer';
    element.setAttribute('aria-label', selectedPoi.name);
    // Pin là overlay HTML nên nó CHE chấm POI bên dưới — không bắt click ở đây
    // thì bấm vào đúng địa điểm đang chọn không mở được panel chi tiết nữa.
    // stopPropagation để cú bấm không lọt xuống bản đồ phía sau.
    element.addEventListener('click', (event) => {
      event.stopPropagation();
      openDetailRef.current(selectedPoi.id, 'map');
    });
    selectedMarkerRef.current = marker;
  }, [selectedPoi]);

  // `origin` cho phép tìm kiếm tại một toạ độ CHƯA kịp vào state. setPosition là
  // bất đồng bộ, nên gọi runSearch ngay sau nó vẫn đọc được `position` cũ trong
  // closure này và sẽ hỏi backend quanh vị trí trước đó.
  async function runSearch(
    searchQuery: string,
    category: string | null,
    origin?: Position,
    // Cùng lý do với `origin`: đổi bán kính rồi tìm lại ngay thì `radius` trong
    // closure này vẫn là giá trị cũ.
    searchRadius: number = radius,
  ) {
    const searchOrigin = origin ?? position;
    // Có `origin` nghĩa là toạ độ vừa lấy từ GPS sau khi người dùng bấm đồng ý;
    // `hasLocationConsent` lúc này còn là giá trị cũ của lần render trước.
    const consent = hasLocationConsent || origin !== undefined;
    setIsLoading(true);
    telemetry.capture({
      event_type: 'search',
      query: searchQuery.trim(),
      location: consent ? searchOrigin : undefined,
      location_consent: consent,
      metadata: {
        radius_meters: searchRadius,
        source: 'search-form',
        category: category ?? undefined,
      },
    });
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/search`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Session-ID': telemetry.sessionId,
        },
        body: JSON.stringify({
          query: searchQuery.trim(),
          latitude: searchOrigin.latitude,
          longitude: searchOrigin.longitude,
          radius: searchRadius,
          limit: 50,
          session_id: telemetry.sessionId,
          category: category ?? undefined,
        }),
      });
      if (!response.ok) throw new Error('Backend chưa sẵn sàng');
      const data = (await response.json()) as ContextualSearchResponse;
      setPois(data.results);
      // Bắn lô impression NGAY TẠI ĐÂY, từ data.results — không dùng useEffect
      // theo [pois] và không đọc state `pois`. Lý do: pois còn được set từ ba
      // luồng khác (POI mẫu lúc mount, nhánh catch offline, sau khi lấy GPS)
      // vốn không có requestId và mang id giả 'poi-001'…; ghi chúng vào
      // ingestion_events sẽ tạo rác mà mọi JOIN với bảng pois lặng lẽ bỏ qua.
      // Gọi thẳng ở đây cũng tránh việc React Strict Mode chạy effect hai lần.
      // Cập nhật ngữ cảnh KỂ CẢ khi 0 kết quả: nếu không, ngữ cảnh của lần tìm
      // TRƯỚC còn nguyên, và cú click kế tiếp bị đóng dấu request_id của một
      // lần tìm kiếm đã bị thay thế — sinh nhãn positive giả.
      const isNewSearch = lastSearchRef.current?.requestId !== data.requestId;
      if (isNewSearch) {
        lastSearchRef.current = {
          requestId: data.requestId,
          ranks: new Map(
            data.results.map((poi, index) => [poi.id, poi.rank ?? index]),
          ),
        };
      }
      if (isNewSearch && data.results.length) {
        telemetry.captureBatch(
          data.results.map((poi, index) => ({
            event_type: 'poi_impression' as const,
            poi_id: poi.id,
            metadata: {
              request_id: data.requestId,
              query: data.query,
              // rank do SERVER gán (sau diversify). Không suy từ chỉ số mảng:
              // spotlightPoi chèn POI vào đầu mảng nên client lệch một bậc.
              rank: poi.rank ?? index,
            },
          })),
        );
      }
      setSelectedPoiId(data.results[0]?.id ?? null);
      setGatewayStatus(`Gateway OK · ${data.requestId.slice(0, 8)}`);
      if (data.parsedLocation.matched && data.parsedLocation.bestMatch) {
        setParserStatus(
          `${data.parsedLocation.bestMatch.canonicalName} · ${Math.round(data.parsedLocation.bestMatch.confidence * 100)}%`,
        );
        mapRef.current?.flyTo({
          center: [data.searchCenter.longitude, data.searchCenter.latitude],
          zoom: 14.5,
          essential: true,
        });
      } else {
        setParserStatus(
          data.parsedLocation.locationText
            ? 'Không nhận ra địa danh'
            : 'Dùng tọa độ thiết bị',
        );
        fitMapToResults(mapRef.current, searchOrigin, data.results);
      }
      setSearchMeta({
        backend: data.retrievalBackend,
        geoFilter: data.geoFilter ?? null,
        ranker: data.ranker,
      });
      // Chữ "PostGIS" từng bị HARD-CODE ở đây, bất kể backend thật đã chạy gì.
      // Người dùng nhìn thấy "kết quả từ PostGIS" trong khi hệ thống đang chạy
      // truy xuất đa kênh qua OpenSearch — giao diện nói sai về kiến trúc của
      // chính nó, ngay dòng chữ dưới ô tìm kiếm. Cùng loại lỗi mà trường
      // `retrievalBackend` được thêm vào để phơi ra, chỉ là lần này chỗ hỏng
      // nằm ở phía hiển thị.
      const duong =
        data.retrievalBackend === 'opensearch'
          ? 'truy xuất đa kênh'
          : 'PostGIS dự phòng';
      setStatus(`${data.results.length} kết quả · ${duong}`);
    } catch {
      const fallback = enrichSamplePois(
        searchOrigin,
        searchQuery,
        searchRadius,
        category,
      );
      // Dữ liệu mẫu không thuộc lần tìm kiếm nào; xoá ngữ cảnh để click sau đó
      // không bị đóng dấu request_id cũ.
      lastSearchRef.current = null;
      setPois(fallback);
      // Xoá metadata: giữ lại thì bảng tín hiệu vẫn khoe "truy xuất đa kênh"
      // trong khi màn hình đang là 6 POI mẫu bịa sẵn.
      setSearchMeta(null);
      setSelectedPoiId(fallback[0]?.id ?? null);
      setStatus(
        `${fallback.length} kết quả mẫu · khởi động backend để dùng PostGIS`,
      );
      setGatewayStatus('Gateway ngoại tuyến · dùng dữ liệu mẫu');
      fitMapToResults(mapRef.current, searchOrigin, fallback);
    } finally {
      setIsLoading(false);
    }
  }

  async function searchNearby() {
    await runSearch(query, selectedCategory);
  }

  function toggleCategory(category: string) {
    const next = selectedCategory === category ? null : category;
    setSelectedCategory(next);
    void runSearch(query, next);
  }

  function useCurrentLocation() {
    if (!navigator.geolocation) {
      setStatus('Trình duyệt không hỗ trợ định vị');
      return;
    }
    setStatus('Đang lấy vị trí của bạn…');
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        // Máy bàn/laptop không có GPS: trình duyệt trả vị trí đoán theo IP với
        // accuracy hàng chục-trăm km. Tin tọa độ đó là bay bản đồ về một huyện
        // ngẫu nhiên và tìm kiếm 0 kết quả — tệ hơn hẳn đứng yên ở trung tâm
        // TP.HCM. Quá ngưỡng thì coi như KHÔNG định vị được, nói rõ sai số.
        if (coords.accuracy > MAX_USABLE_ACCURACY_METERS) {
          setGpsStatus(
            `Vị trí quá mờ (±${Math.round(coords.accuracy / 1000)} km) · dùng vị trí mặc định`,
          );
          setStatus(
            'Máy không định vị chính xác được — đang dùng trung tâm TP.HCM',
          );
          return;
        }
        const nextPosition = {
          latitude: coords.latitude,
          longitude: coords.longitude,
        };
        setPosition(nextPosition);
        setHasLocationConsent(true);
        setGpsStatus(`GPS chính xác ±${Math.round(coords.accuracy)} m`);
        telemetry.capture({
          event_type: 'location_ping',
          location: { ...nextPosition, accuracy_meters: coords.accuracy },
          location_consent: true,
          metadata: { source: 'browser-geolocation', high_accuracy: true },
        });
        mapRef.current?.flyTo({
          center: [nextPosition.longitude, nextPosition.latitude],
          zoom: 14,
        });
        // Trước đây chỗ này nạp SAMPLE_POIS — 6 địa điểm hard-code quanh Quận 1.
        // Người dùng ở Thủ Đức hay Hà Nội bấm "Vị trí của tôi" vẫn nhận đúng 6 POI
        // đó, kèm khoảng cách tính từ toạ độ thật tới chúng: giao diện trông như
        // đang chạy, số liệu thì vô nghĩa. Hỏi lại backend quanh toạ độ mới, truyền
        // thẳng nextPosition vì setPosition chưa kịp vào state.
        void runSearch(query, selectedCategory, nextPosition);
      },
      () => {
        setGpsStatus('GPS bị từ chối hoặc không khả dụng');
        setStatus('Không thể lấy vị trí — đang dùng trung tâm TP.HCM');
      },
      { enableHighAccuracy: true, timeout: 8_000 },
    );
  }

  // Tự hỏi vị trí NGAY KHI MỞ TRANG thay vì đứng ở Quận 1 chờ người dùng bấm
  // "Vị trí của tôi". Trình duyệt tự lo phần đồng ý: lần đầu nó hiện hộp xin
  // quyền, đã cho phép từ trước thì vào thẳng, đã chặn thì rơi vào nhánh lỗi
  // của useCurrentLocation và bản đồ đứng yên ở mặc định — không hỏi lại, không
  // vòng lặp. Ref chặn StrictMode chạy effect hai lần: hai getCurrentPosition
  // song song là hai lượt runSearch giẫm nhau.
  const autoLocatedRef = useRef(false);
  useEffect(() => {
    if (autoLocatedRef.current) return;
    autoLocatedRef.current = true;
    useCurrentLocation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Ping mới chỉ được gửi khi đã đủ xa lần trước HOẶC đã đủ lâu. Không có bộ
  // lọc này thì watchPosition bắn mỗi lần GPS nhích một mét: hàng nghìn sự kiện
  // rác mỗi phiên, và bộ chấm chất lượng toạ độ sẽ thấy một chuỗi dịch chuyển
  // vi mô thay vì một hành trình.
  const PING_MIN_INTERVAL_MS = 15_000;
  const PING_MIN_DISTANCE_M = 50;

  function stopWatching() {
    if (watchIdRef.current !== null) {
      navigator.geolocation.clearWatch(watchIdRef.current);
      watchIdRef.current = null;
    }
    lastPingRef.current = null;
    setIsWatching(false);
    setWatchStatus('Theo dõi vị trí: tắt');
  }

  function startWatching() {
    if (!navigator.geolocation) {
      setWatchStatus('Trình duyệt không hỗ trợ định vị');
      return;
    }
    const id = navigator.geolocation.watchPosition(
      ({ coords }) => {
        const nextPosition = {
          latitude: coords.latitude,
          longitude: coords.longitude,
        };
        const now = Date.now();
        const previous = lastPingRef.current;
        const moved = previous
          ? distanceInMeters(previous.position, nextPosition)
          : Infinity;
        const elapsed = previous ? now - previous.at : Infinity;
        if (moved < PING_MIN_DISTANCE_M && elapsed < PING_MIN_INTERVAL_MS)
          return;

        lastPingRef.current = { at: now, position: nextPosition };
        setPosition(nextPosition);
        setHasLocationConsent(true);
        setGpsStatus(`GPS chính xác ±${Math.round(coords.accuracy)} m`);
        setWatchStatus(
          `Theo dõi vị trí: bật · ping lúc ${new Date(now).toLocaleTimeString('vi-VN')}`,
        );
        telemetry.capture({
          event_type: 'location_ping',
          location: { ...nextPosition, accuracy_meters: coords.accuracy },
          location_consent: true,
          metadata: {
            source: 'browser-geolocation-watch',
            high_accuracy: true,
            moved_meters: Number.isFinite(moved) ? Math.round(moved) : null,
          },
        });
      },
      () => {
        setWatchStatus('Theo dõi vị trí: bị từ chối');
        stopWatching();
      },
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 5_000 },
    );
    watchIdRef.current = id;
    setIsWatching(true);
    setWatchStatus('Theo dõi vị trí: bật · đang chờ ping đầu tiên');
  }

  async function toggleGeofence(poi: Poi) {
    const sessionId = telemetryState.sessionId;
    if (!sessionId) {
      setStatus('Chưa có phiên — thử lại sau một nhịp');
      return;
    }
    // POI mẫu hard-code (poi-001…) không có trong database, nên backend từ chối
    // bằng 422. Chặn ngay ở đây và nói đúng nguyên nhân: báo "kiểm tra kết nối
    // tới API" trong khi API đang trả lời bình thường là đẩy người dùng đi tìm
    // sai chỗ — đúng loại thông báo lỗi tệ nhất.
    if (!UUID_PATTERN.test(poi.id)) {
      setStatus(
        'Đây là địa điểm mẫu, chưa có trong dữ liệu thật — hãy tìm kiếm trước',
      );
      return;
    }

    const existing = geofences.get(poi.id);
    try {
      if (existing) {
        await fetch(`${API_BASE_URL}/api/v1/geofences/${existing}`, {
          method: 'DELETE',
          headers: { 'X-Session-ID': sessionId },
        });
        setGeofences((current) => {
          const next = new Map(current);
          next.delete(poi.id);
          return next;
        });
        setStatus(`Đã bỏ nhắc "${poi.name}"`);
        return;
      }
      // Xin quyền thông báo NGAY TRONG cú bấm này. Xin lúc tải trang là cách
      // nhanh nhất để bị từ chối vĩnh viễn, và quyền đã bị chặn thì không xin
      // lại được bằng code.
      const allowed = await proximity.requestPermission();
      const response = await fetch(`${API_BASE_URL}/api/v1/geofences`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Session-ID': sessionId,
        },
        // KHÔNG gửi toạ độ: tâm vùng do backend lấy thẳng từ pois.location.
        body: JSON.stringify({ poi_id: poi.id, radius_meters: 300 }),
      });
      if (!response.ok) {
        // Đọc lý do server đưa ra thay vì nuốt nó: 422 (POI không tồn tại) và
        // 503 (backend chết) cần hai hành động khác hẳn nhau từ người dùng.
        const detail = await response.text();
        throw new Error(`HTTP ${response.status} ${detail.slice(0, 160)}`);
      }
      const created = (await response.json()) as { id: string };
      setGeofences((current) => new Map(current).set(poi.id, created.id));
      setStatus(
        allowed
          ? `Sẽ nhắc khi bạn tới gần "${poi.name}" (300 m)`
          : `Đã lưu vùng nhắc "${poi.name}", nhưng trình duyệt đang chặn thông báo`,
      );
    } catch (error) {
      setStatus(`Không lưu được vùng nhắc — ${(error as Error).message}`);
    }
  }

  function startNavigation(poi: Poi) {
    // Nút chính luôn ở trong ứng dụng. Trước đây khi route chưa kịp tải hoặc
    // POI mẫu không có UUID, nhánh cuối tự mở Google Maps — đúng cú nhảy trang
    // mà người dùng không mong đợi.
    const inAppRoute = route && route.poiId === poi.id ? route : null;
    telemetry.capture({
      event_type: 'navigation_start',
      poi_id: poi.id,
      metadata: {
        provider: 'in-app',
        mode: transportMode,
        route_ready: Boolean(inAppRoute),
      },
    });
    if (inAppRoute) {
      setShowSteps(true);
      const map = mapRef.current;
      if (map) {
        const bounds = new maplibregl.LngLatBounds();
        for (const point of inAppRoute.geometry.coordinates) {
          bounds.extend(point as [number, number]);
        }
        // Cùng padding với effect vẽ tuyến — thẻ POI và bảng điều khiển che
        // hai góc bản đồ.
        map.fitBounds(bounds, {
          padding: { top: 90, bottom: 190, left: 60, right: 330 },
          maxZoom: ROUTE_MAX_ZOOM,
          duration: 700,
        });
      }
      return;
    }
    if (routeStatus === 'loading') {
      setStatus('Đang tính tuyến đường trong ứng dụng…');
    } else if (routeStatus === 'off') {
      setStatus('Chưa khởi động dịch vụ định tuyến OSRM');
    } else {
      setStatus('Không tìm được đường bộ tới địa điểm này');
    }
  }

  return (
    <main className="min-h-screen bg-transparent text-foreground">
      <header className="border-b border-emerald-950/10 bg-white/90 px-4 py-3 backdrop-blur-xl sm:px-6 dark:border-white/10 dark:bg-slate-950/80">
        <div className="mx-auto flex max-w-[1500px] items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-2xl bg-primary text-primary-foreground shadow-sm">
              <Compass className="size-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-lg font-bold tracking-tight">Nearby</span>
                <Badge variant="secondary">Tầng 1-3 · Live</Badge>
              </div>
              <p className="hidden text-xs text-muted-foreground sm:block">
                Tìm kiếm địa điểm theo vị trí
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="hidden sm:inline">TP. Hồ Chí Minh</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => about.setOpen(true)}
              aria-label="Giới thiệu đồ án"
            >
              <Info data-icon="inline-start" />
              <span className="hidden sm:inline">Giới thiệu</span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={toggleTheme}
              aria-label="Chuyển giao diện sáng/tối"
            >
              {theme === 'dark' ? (
                <Sun className="size-4" />
              ) : (
                <Moon className="size-4" />
              )}
            </Button>
            <Button variant="outline" size="sm" onClick={useCurrentLocation}>
              <LocateFixed data-icon="inline-start" />
              Vị trí của tôi
            </Button>
            <Button
              variant={isWatching ? 'default' : 'outline'}
              size="sm"
              onClick={() => (isWatching ? stopWatching() : startWatching())}
              title={watchStatus}
              aria-pressed={isWatching}
            >
              <Route data-icon="inline-start" />
              {isWatching ? 'Đang theo dõi' : 'Theo dõi vị trí'}
            </Button>
          </div>
        </div>
      </header>
      <AboutDialog open={about.open} onOpenChange={about.onOpenChange} />

      <section className="mx-auto grid max-w-[1500px] gap-4 p-4 lg:h-[calc(100vh-65px)] lg:grid-cols-[430px_minmax(0,1fr)] lg:p-5">
        <aside className="flex min-h-0 flex-col gap-4">
          <Card className="shrink-0 border-0 shadow-[0_12px_40px_rgb(14_68_48/8%)] ring-emerald-950/10 lg:max-h-[50%] lg:overflow-y-auto">
            <CardHeader className="px-5 pt-5 pb-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-xl font-bold">
                    Bạn muốn đi đâu?
                  </CardTitle>
                  <CardDescription>
                    Tìm địa điểm phù hợp trong vài giây.
                  </CardDescription>
                </div>
                <div className="grid size-10 place-items-center rounded-full bg-emerald-50 text-primary dark:bg-emerald-500/10">
                  <Sparkles className="size-5" />
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 px-5 pb-5">
              <form
                className="flex gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  void searchNearby();
                }}
              >
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    className="h-11 rounded-xl bg-white pl-9 dark:bg-input/40"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Cà phê, phở, công viên…"
                    aria-label="Từ khóa tìm kiếm"
                  />
                </div>
                <Button
                  className="h-11 rounded-xl px-4"
                  type="submit"
                  disabled={isLoading}
                >
                  {isLoading ? 'Đang tìm…' : 'Tìm'}
                </Button>
              </form>
              <div className="flex items-center justify-between gap-3 rounded-xl bg-muted/65 px-3 py-2.5">
                <div className="flex items-center gap-2 text-sm">
                  <SlidersHorizontal className="size-4 text-primary" />
                  <span>Bán kính</span>
                </div>
                <select
                  className="rounded-lg border border-border bg-white px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-ring/40 dark:bg-input/40"
                  value={radius}
                  onChange={(event) => {
                    // Trước đây chỉ setRadius: bán kính mới chỉ có hiệu lực ở lần
                    // bấm "Tìm" kế tiếp, nên đổi 1 km <-> 10 km trông như không
                    // làm gì với danh sách đang hiện.
                    const nextRadius = Number(event.target.value);
                    setRadius(nextRadius);
                    void runSearch(
                      query,
                      selectedCategory,
                      undefined,
                      nextRadius,
                    );
                  }}
                  aria-label="Bán kính tìm kiếm"
                >
                  <option value={1_000}>1 km</option>
                  <option value={3_000}>3 km</option>
                  <option value={5_000}>5 km</option>
                  <option value={10_000}>10 km</option>
                </select>
              </div>
              <div className="flex flex-col gap-2 rounded-xl bg-muted/65 px-3 py-2.5">
                <div className="flex items-center gap-2 text-sm">
                  <Star className="size-4 fill-amber-400 text-amber-500" />
                  <span>Đánh giá tối thiểu</span>
                </div>
                <div
                  className="flex flex-wrap gap-1.5"
                  role="radiogroup"
                  aria-label="Lọc theo đánh giá tối thiểu"
                >
                  {MIN_RATING_OPTIONS.map((value) => (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={minRating === value}
                      onClick={() => setMinRating(value)}
                      className={`inline-flex items-center gap-1 ${chipClass(minRating === value)}`}
                    >
                      {value === 0 ? (
                        'Tất cả'
                      ) : (
                        <>
                          {value.toLocaleString('vi-VN')}
                          <Star className="size-3 fill-current" />+
                        </>
                      )}
                    </button>
                  ))}
                </div>
              </div>
              {categoryOptions.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedCategory(null);
                      void runSearch(query, null);
                    }}
                    className={chipClass(selectedCategory === null)}
                  >
                    Tất cả
                  </button>
                  {categoryOptions.map((option) => (
                    <button
                      key={option.category}
                      type="button"
                      onClick={() => toggleCategory(option.category)}
                      className={chipClass(
                        selectedCategory === option.category,
                      )}
                    >
                      {option.categoryLabel}
                    </button>
                  ))}
                </div>
              )}
              <p className="text-xs text-muted-foreground" aria-live="polite">
                {status}
              </p>
              {/* Dải tín hiệu CẤP TRUY VẤN — những thứ dùng chung cho cả lượt
                  tìm, không lặp lại trên từng dòng kết quả. Mỗi chip hoặc có số
                  kèm đơn vị, hoặc là một câu nói rõ vì sao chưa có dữ liệu.
                  Không chip nào in ra một số 0 trần trụi. */}
              {(searchMeta || queryWeather) && (
                <div className="flex flex-wrap gap-1.5 text-[11px]">
                  {searchMeta && (
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-1 font-medium ${
                        searchMeta.backend === 'opensearch'
                          ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
                          : 'bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300'
                      }`}
                      title={
                        searchMeta.backend === 'opensearch'
                          ? 'BM25 + vector + geo + H3 + trending, gộp bằng Reciprocal Rank Fusion'
                          : 'OpenSearch không dùng được — đang chạy đường PostGIS dự phòng'
                      }
                    >
                      {searchMeta.backend === 'opensearch'
                        ? 'Đa kênh · RRF'
                        : 'PostGIS dự phòng'}
                    </span>
                  )}
                  {h3Badge && (
                    <span
                      className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-1 font-medium text-sky-700 dark:bg-sky-500/15 dark:text-sky-300"
                      title="Kênh 2 lọc bằng vành hexagon H3 (terms trên chỉ mục đảo) thay vì tính khoảng cách từng document"
                    >
                      {h3Badge}
                    </span>
                  )}
                  {queryWeather && (
                    <span
                      className="inline-flex items-center gap-1 rounded-full bg-violet-50 px-2 py-1 font-medium text-violet-700 dark:bg-violet-500/15 dark:text-violet-300"
                      title={
                        queryWeather.isWet
                          ? 'Trời mưa: hạ điểm địa điểm ngoài trời, nâng địa điểm có mái che'
                          : 'Trời khô: thời tiết không tác động tới thứ hạng'
                      }
                    >
                      {weatherLabel(queryWeather)}
                    </span>
                  )}
                  {searchMeta?.ranker && (
                    <span
                      className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-1 font-medium text-muted-foreground"
                      title="Bộ xếp hạng ĐÃ CHẠY THẬT, không phải cái được yêu cầu"
                    >
                      {searchMeta.ranker === 'ltr'
                        ? 'LambdaMART'
                        : 'Tuyến tính 9 tín hiệu'}
                    </span>
                  )}
                </div>
              )}
              <div className="grid grid-cols-2 gap-2 xl:hidden">
                <div className="rounded-xl border border-border/70 bg-white px-3 py-2 dark:bg-card">
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Định vị
                  </p>
                  <p className="mt-1 truncate text-xs font-medium">
                    {gpsStatus}
                  </p>
                </div>
                <div className="rounded-xl border border-border/70 bg-white px-3 py-2 dark:bg-card">
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Event stream
                  </p>
                  <p className="mt-1 text-xs font-medium">
                    {telemetryState.delivered} gửi · {telemetryState.queued} chờ
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1">
            {showDiscovery &&
              trending &&
              (trending.pois.length > 0 || trending.queries.length > 0) && (
                <div className="shrink-0 space-y-2 rounded-2xl border border-emerald-950/10 bg-white/70 p-3 dark:border-white/10 dark:bg-card/70">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                    <Flame className="size-3.5 text-orange-500" /> Xu hướng gần
                    đây
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {trending.queries.map((item) => (
                      <button
                        key={item.query}
                        type="button"
                        onClick={() => {
                          setQuery(item.query);
                          void runSearch(item.query, selectedCategory);
                        }}
                        className={chipClass(false)}
                      >
                        {item.query}
                      </button>
                    ))}
                    {trending.pois.map((poi) => (
                      <button
                        key={poi.id}
                        type="button"
                        onClick={() => spotlightPoi(poi, 'trending')}
                        className={chipClass(false)}
                      >
                        {poi.name}
                      </button>
                    ))}
                  </div>
                </div>
              )}

            {showDiscovery && recommendations.length > 0 && (
              <div className="shrink-0 space-y-2">
                <div className="flex items-center gap-1.5 px-1 text-xs font-semibold text-muted-foreground">
                  <Sparkles className="size-3.5 text-primary" /> Gợi ý cho bạn
                </div>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {recommendations.map((poi) => (
                    <button
                      key={poi.id}
                      type="button"
                      onClick={() => spotlightPoi(poi, 'recommendation')}
                      className="w-[180px] shrink-0 rounded-xl border border-emerald-950/10 bg-white p-3 text-left transition-all hover:-translate-y-0.5 hover:shadow-md dark:border-white/10 dark:bg-card"
                    >
                      <p className="truncate text-sm font-semibold">
                        {poi.name}
                      </p>
                      <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">
                        {poi.reason}
                      </p>
                      <div className="mt-2 flex items-center gap-2 text-[11px]">
                        {poi.rating !== null && (
                          <span className="flex items-center gap-0.5 font-semibold text-amber-600">
                            <Star className="size-3 fill-current" />{' '}
                            {poi.rating.toFixed(1)}
                          </span>
                        )}
                        <span className="text-muted-foreground">
                          {formatDistance(poi.distanceMeters)}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="shrink-0 flex items-center justify-between px-1">
              <h2 className="font-semibold">Địa điểm gần bạn</h2>
              <span className="text-xs text-muted-foreground">
                {minRating > 0 && ratedPois.length !== pois.length
                  ? `${ratedPois.length}/${pois.length} kết quả`
                  : `${pois.length} kết quả`}
              </span>
            </div>
            <div className="grid shrink-0 gap-3">
              {/* Số hiển thị là VỊ TRÍ TRONG DANH SÁCH ĐANG THẤY, không phải
                  poi.rank. spotlightPoi chèn POI từ trending/gợi ý vào đầu mảng
                  và POI đó không có rank, nên dùng `poi.rank ?? index` sẽ cho
                  hai dòng cùng số 1. Thứ hạng server vẫn được ghi riêng vào
                  telemetry lúc impression — đó mới là số dùng để phân tích
                  position bias, và nó không cần khớp với số đang hiển thị. */}
              {ratedPois.map((poi, index) => (
                <button
                  type="button"
                  key={poi.id}
                  onClick={() => focusPoi(poi)}
                  aria-label={`Chọn địa điểm ${poi.name}`}
                  className={`group w-full rounded-2xl border bg-white p-4 text-left transition-all hover:-translate-y-0.5 hover:shadow-lg dark:bg-card ${
                    selectedPoiId === poi.id
                      ? 'border-primary/45 shadow-[0_12px_35px_rgb(15_138_98/14%)] ring-2 ring-primary/10'
                      : 'border-emerald-950/10 shadow-sm dark:border-white/10'
                  }`}
                >
                  <div className="flex gap-3">
                    <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-emerald-50 font-bold text-primary dark:bg-emerald-500/10">
                      {index + 1}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <h3 className="font-semibold leading-tight">
                            {poi.name}
                          </h3>
                          <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">
                            {poi.address}
                          </p>
                        </div>
                        <Badge
                          variant="outline"
                          className="shrink-0 bg-white dark:bg-card"
                        >
                          {poi.categoryLabel}
                        </Badge>
                      </div>
                      <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">
                        {poi.description}
                      </p>
                      <div className="mt-3 flex items-center gap-3 text-xs">
                        {poi.rating === null ? (
                          <span className="text-muted-foreground">
                            Chưa có đánh giá
                          </span>
                        ) : (
                          <>
                            <span className="flex items-center gap-1 font-semibold text-amber-600">
                              <Star className="size-3.5 fill-current" />{' '}
                              {poi.rating.toFixed(1)}
                            </span>
                            <span className="text-muted-foreground">
                              {poi.reviewCount} đánh giá
                            </span>
                          </>
                        )}
                        <span className="ml-auto flex items-center gap-1 font-medium text-primary">
                          <Navigation className="size-3.5" />
                          {formatDistance(poi.distanceMeters)}
                        </span>
                      </div>
                      {/* Tín hiệu riêng của TỪNG POI. traffic đổi theo vị trí
                          nên nằm ở đây, không nằm ở dải cấp truy vấn. */}
                      {(poi.traffic?.isPeakHour ||
                        (poi.liveNearbyUsers ?? 0) > 0 ||
                        poi.trendingScope === 'hex' ||
                        poi.retrievalChannels?.includes('h3')) && (
                        <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                          {poi.traffic?.isPeakHour && (
                            <span
                              className="rounded-full bg-orange-50 px-1.5 py-0.5 font-medium text-orange-700 dark:bg-orange-500/15 dark:text-orange-300"
                              title={`Giờ cao điểm · thời gian đi đã nhân ${poi.traffic.factor.toFixed(2)} lần`}
                            >
                              Giờ cao điểm
                            </span>
                          )}
                          {(poi.liveNearbyUsers ?? 0) > 0 && (
                            <span
                              className="rounded-full bg-rose-50 px-1.5 py-0.5 font-medium text-rose-700 dark:bg-rose-500/15 dark:text-rose-300"
                              title="Số phiên đang hoạt động trong 300 m quanh địa điểm, đếm bằng GEOSEARCH trên Redis"
                            >
                              {poi.liveNearbyUsers} người quanh đây
                            </span>
                          )}
                          {poi.trendingScope === 'hex' && (
                            <span
                              className="rounded-full bg-amber-50 px-1.5 py-0.5 font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
                              title="Đang hot trong chính ô H3 quanh bạn, không phải hot toàn thành phố"
                            >
                              Hot quanh đây
                            </span>
                          )}
                          {poi.retrievalChannels &&
                            poi.retrievalChannels.length > 1 && (
                              <span
                                className="rounded-full bg-muted px-1.5 py-0.5 font-medium text-muted-foreground"
                                title={`Lọt vào ứng viên qua ${poi.retrievalChannels.length} kênh: ${poi.retrievalChannels.join(', ')}`}
                              >
                                {poi.retrievalChannels.length} kênh
                              </span>
                            )}
                        </div>
                      )}
                      {(poi.openNow != null || poi.etaMinutes) && (
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                          {poi.openNow === true &&
                          poi.closesInMinutes != null &&
                          poi.closesInMinutes <= 45 ? (
                            <span className="rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-400">
                              Sắp đóng · {poi.closesInMinutes} phút
                            </span>
                          ) : poi.openNow === true ? (
                            <span className="rounded-full bg-emerald-50 px-2 py-0.5 font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400">
                              Đang mở
                            </span>
                          ) : poi.openNow === false ? (
                            <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-muted-foreground">
                              {poi.opensInMinutes != null &&
                              poi.opensInMinutes <= 120
                                ? `Mở sau ${poi.opensInMinutes} phút`
                                : 'Đóng cửa'}
                            </span>
                          ) : null}
                          {poi.etaMinutes && (
                            <span className="flex items-center gap-1 text-muted-foreground">
                              <Bike className="size-3.5" />{' '}
                              {poi.etaMinutes.motorbike} phút
                              <Clock className="ml-0.5 size-3 opacity-60" />
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </button>
              ))}
              {ratedPois.length === 0 && (
                <div className="rounded-2xl border border-dashed border-border bg-white/70 p-8 text-center dark:bg-card/70">
                  <MapPin className="mx-auto size-8 text-muted-foreground" />
                  <p className="mt-3 font-medium">Chưa có địa điểm phù hợp</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {minRating > 0 && pois.length > 0
                      ? `Không có địa điểm nào đạt từ ${minRating.toLocaleString('vi-VN')}★ — hãy hạ mức đánh giá tối thiểu.`
                      : 'Thử từ khóa khác hoặc tăng bán kính tìm kiếm.'}
                  </p>
                </div>
              )}
            </div>
          </div>
        </aside>

        <section className="relative min-h-[520px] overflow-hidden rounded-[26px] border border-emerald-950/10 bg-slate-100 shadow-[0_18px_60px_rgb(14_68_48/12%)] lg:min-h-0">
          <div className="map-fallback absolute inset-0" aria-hidden="true" />
          <div
            ref={mapContainerRef}
            className="map-canvas-host absolute inset-0"
            aria-label="Bản đồ địa điểm"
          />
          <div className="pointer-events-none absolute left-4 top-4 z-10 rounded-xl border border-white/70 bg-white/90 px-3 py-2 text-xs shadow-lg backdrop-blur-md dark:border-white/10 dark:bg-card/90">
            <div className="flex items-center gap-2 font-medium">
              <span className="size-2 rounded-full bg-sky-500" /> Vị trí của bạn
              <span className="ml-2 size-2 rounded-full bg-orange-500" /> POI
              <span className="ml-2 size-2 rounded-full bg-emerald-500" /> Cụm
              <span className="ml-2 size-2 rounded-full bg-sky-500/60" /> Vành
              H3
            </div>
          </div>
          <div className="absolute right-4 top-4 z-10 hidden w-[300px] rounded-2xl border border-white/75 bg-slate-950/88 p-4 text-white shadow-2xl backdrop-blur-xl xl:block">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-emerald-300">
                  Client & ingestion
                </p>
                <h2 className="mt-1 font-semibold">Bảng điều khiển tầng 1</h2>
              </div>
              <div className="relative grid size-9 place-items-center rounded-xl bg-emerald-400/15 text-emerald-300">
                <Radio className="size-4" />
                <span className="absolute right-1 top-1 size-1.5 animate-pulse rounded-full bg-emerald-300" />
              </div>
            </div>
            <div className="mt-4 space-y-3 text-xs">
              <div className="flex gap-3">
                <LocateFixed className="mt-0.5 size-4 shrink-0 text-sky-300" />
                <div className="min-w-0">
                  <p className="font-medium text-white">GPS & quyền riêng tư</p>
                  <p className="mt-0.5 truncate text-slate-300">{gpsStatus}</p>
                </div>
              </div>
              <div className="flex gap-3">
                <ShieldCheck className="mt-0.5 size-4 shrink-0 text-violet-300" />
                <div className="min-w-0">
                  <p className="font-medium text-white">API Gateway</p>
                  <p className="mt-0.5 truncate text-slate-300">
                    {gatewayStatus}
                  </p>
                </div>
              </div>
              <div className="flex gap-3">
                <Activity className="mt-0.5 size-4 shrink-0 text-amber-300" />
                <div className="min-w-0">
                  <p className="font-medium text-white">Geo-parser</p>
                  <p className="mt-0.5 truncate text-slate-300">
                    {parserStatus}
                  </p>
                </div>
              </div>
              <div className="flex gap-3">
                <DatabaseZap className="mt-0.5 size-4 shrink-0 text-emerald-300" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <p className="font-medium text-white">Event ingestion</p>
                    {telemetryState.transport === 'online' ? (
                      <CheckCircle2 className="size-3.5 text-emerald-300" />
                    ) : null}
                  </div>
                  <p className="mt-0.5 text-slate-300">
                    {telemetryState.delivered} đã gửi · {telemetryState.queued}{' '}
                    đang chờ ·{' '}
                    {telemetryState.transport === 'online'
                      ? 'Redis Stream'
                      : telemetryState.transport === 'fallback'
                        ? 'Postgres fallback'
                        : telemetryState.transport === 'offline'
                          ? 'ngoại tuyến'
                          : 'sẵn sàng'}
                  </p>
                </div>
              </div>
            </div>
            <div className="mt-4 rounded-xl bg-white/8 px-3 py-2 text-[10px] text-slate-300">
              Session{' '}
              {telemetryState.sessionId
                ? telemetryState.sessionId.slice(0, 8)
                : 'đang tạo'}{' '}
              · batch ≤ 50 events
            </div>
          </div>
          {selectedPoi && (
            <div className="absolute bottom-5 left-5 right-5 z-10 rounded-2xl border border-white/70 bg-white/92 p-4 shadow-xl backdrop-blur-xl sm:right-auto sm:w-[360px] dark:border-white/10 dark:bg-card/95">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <Badge variant="secondary">Đang chọn</Badge>
                  <h2 className="mt-2 text-lg font-bold">{selectedPoi.name}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {selectedPoi.address}
                  </p>
                </div>
                <div className="flex shrink-0 items-start gap-1.5">
                  <div className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground">
                    <MapPin className="size-5" />
                  </div>
                  <button
                    type="button"
                    aria-label="Đóng thẻ địa điểm"
                    onClick={() => setSelectedPoiId(null)}
                    className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <X className="size-4" />
                  </button>
                </div>
              </div>
              <div className="mt-3 flex items-center justify-between border-t border-border pt-3 text-sm">
                <span className="font-medium text-amber-600">
                  {selectedPoi.rating === null
                    ? 'Chưa có đánh giá'
                    : `★ ${selectedPoi.rating.toFixed(1)}`}
                </span>
                <span>{formatDistance(selectedPoi.distanceMeters)}</span>
                <Button size="sm" onClick={() => startNavigation(selectedPoi)}>
                  Chỉ đường
                </Button>
              </div>
              {/* Chọn phương tiện — mỗi phương tiện gọi một đồ thị OSRM
                  riêng (car / foot / motorbike, xem docker-compose.yml). Đổi
                  lựa chọn tự kích hoạt lại effect tính tuyến ở trên
                  (transportMode nằm trong deps). */}
              <div className="mt-3 flex gap-1.5 border-t border-border pt-3">
                {TRANSPORT_MODES.map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    onClick={() => setTransportMode(item.value)}
                    className={`flex-1 rounded-lg border px-2 py-1.5 text-xs font-medium transition-colors ${
                      transportMode === item.value
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'border-border text-muted-foreground hover:bg-muted'
                    }`}
                  >
                    {item.icon} {item.label}
                  </button>
                ))}
              </div>
              {/* Tuyến đường thật từ OSRM tự dựng. Ba trạng thái còn lại đều nói
                  rõ VÌ SAO chưa có tuyến, thay vì để ô trống — người dùng không
                  phân biệt được "đang tính" với "hỏng" nếu cả hai đều là khoảng
                  trắng. */}
              {routeStatus === 'loading' && (
                <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">
                  Đang tính đường đi…
                </p>
              )}
              {routeStatus === 'off' && (
                <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">
                  Chưa khởi động dữ liệu định tuyến nội bộ.
                </p>
              )}
              {routeStatus === 'none' && (
                <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">
                  Không tìm được đường bộ tới địa điểm này.
                </p>
              )}
              {route && route.poiId === selectedPoi.id && (
                <div className="mt-3 space-y-2 border-t border-border pt-3">
                  <div className="flex items-center gap-3">
                    <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
                      <Route className="size-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold">
                        {route.durationMinutes} phút ·{' '}
                        {formatDistance(route.distanceMeters)}
                      </p>
                      {/* Nói rõ đây là ĐƯỜNG ĐI THẬT chứ không phải đường chim
                          bay — con số cũ (etaMinutes) tính bằng khoảng cách
                          thẳng chia vận tốc cố định nên luôn lạc quan.
                          `approximate` (backend bật khi phải mượn đồ thị ô tô)
                          xét TRƯỚC tên hồ sơ: im lặng ở đây là lừa người dùng
                          rằng hệ thống đo đúng phương tiện họ chọn. Phải có
                          nhánh 'motorbike' riêng — thiếu nó thì tuyến xe máy
                          THẬT bị ghi nhãn "hồ sơ ô tô", sai theo hướng ngược
                          lại với cảnh báo xấp xỉ. */}
                      <p className="text-[11px] text-muted-foreground">
                        {route.approximate
                          ? 'Tuyến ô tô (xấp xỉ cho xe máy)'
                          : route.mode === 'foot'
                            ? 'Theo đường thật, hồ sơ đi bộ'
                            : route.mode === 'motorbike'
                              ? 'Theo đường thật, hồ sơ xe máy'
                              : 'Theo đường thật, hồ sơ ô tô'}
                        {route.cached ? ' · từ cache' : ''}
                      </p>
                    </div>
                    {route.steps.length > 0 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setShowSteps((current) => !current)}
                      >
                        {showSteps ? 'Ẩn' : `${route.steps.length} bước`}
                      </Button>
                    )}
                  </div>
                  {showSteps && (
                    <ol className="max-h-44 space-y-1.5 overflow-y-auto pr-1 text-xs">
                      {route.steps.map((step, index) => (
                        <li
                          key={`${index}-${step.text}`}
                          className="flex items-start gap-2 rounded-lg bg-muted/60 px-2.5 py-1.5"
                        >
                          <span className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-full bg-primary/15 text-[9px] font-bold text-primary">
                            {index + 1}
                          </span>
                          <span className="min-w-0 flex-1">{step.text}</span>
                          <span className="shrink-0 tabular-nums text-muted-foreground">
                            {formatDistance(step.distanceMeters)}
                          </span>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              )}
              {/* flex-wrap vì hai nút đều whitespace-nowrap (cva gốc của Button)
                  nên min-width:auto ghim sàn cả hàng ở ~263px, flex-1 co không
                  nổi. Thẻ chỉ rộng "viewport - 104px", tức máy 320-360px còn
                  216-256px: không cho xuống dòng là nút thò ra ngoài viền thẻ,
                  đè lên bản đồ rồi bị overflow-hidden của khung bản đồ cắt cụt
                  chữ. Từ ~367px trở lên vẫn nằm gọn một hàng như cũ. */}
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  className="shrink-0"
                  onClick={() => openDetail(selectedPoi.id, 'overlay')}
                >
                  <Info data-icon="inline-start" />
                  Xem chi tiết
                </Button>
                <Button
                  variant={
                    geofences.has(selectedPoi.id) ? 'default' : 'outline'
                  }
                  size="sm"
                  className="flex-1"
                  onClick={() => void toggleGeofence(selectedPoi)}
                >
                  {geofences.has(selectedPoi.id) ? (
                    <BellRing data-icon="inline-start" />
                  ) : (
                    <Bell data-icon="inline-start" />
                  )}
                  {geofences.has(selectedPoi.id)
                    ? 'Đang nhắc · 300 m'
                    : 'Nhắc tôi khi tới gần'}
                </Button>
              </div>
              {geofences.size > 0 && (
                <p className="mt-2 text-[11px] text-muted-foreground">
                  {proximity.connected
                    ? `Đang chờ thông báo · ${geofences.size} địa điểm`
                    : 'Kênh thông báo chưa kết nối'}
                  {proximity.permission === 'denied' &&
                    ' · trình duyệt đang chặn quyền thông báo'}
                </p>
              )}
            </div>
          )}
          {/* Panel chi tiết. z-30 để nằm trên "Bảng điều khiển tầng 1" và thẻ
              "Đang chọn" (cả hai z-10) — panel che gần nửa bản đồ, nếu bị hai
              lớp kia đè lên thì chữ chồng chữ. `inset-y-0` cho nó chiều cao xác
              định: panel bên trong dùng size-full + cột flex cuộn trong, thiếu
              chiều cao của cha là nó sập còn 0px.
              w-[92%] trên điện thoại: phủ kín 100% thì panel trông như đã
              chuyển sang một trang khác và người dùng đi tìm nút Lùi thay vì
              nút đóng ngay trên đầu panel. */}
          {detailPoiId && (
            <div className="poi-detail-slide absolute inset-y-0 right-0 z-30 w-[92%] border-l border-emerald-950/10 bg-white shadow-[-20px_0_60px_rgb(14_68_48/18%)] sm:w-[420px] dark:border-white/10 dark:bg-card">
              <PoiDetailPanel
                key={detailPoiId}
                detail={poiDetail}
                photos={poiPhotos}
                loading={detailLoading}
                error={detailError}
                routeSummary={detailRouteSummary}
                isGeofenced={geofences.has(detailPoiId)}
                apiBaseUrl={API_BASE_URL}
                sessionId={telemetryState.sessionId}
                onClose={closeDetail}
                onDirections={() => {
                  if (detailAsPoi) startNavigation(detailAsPoi);
                }}
                onToggleGeofence={() => {
                  if (detailAsPoi) void toggleGeofence(detailAsPoi);
                }}
                onSelectSimilar={(poiId) => {
                  // Địa điểm tương tự thường KHÔNG nằm trong `pois` của lần tìm
                  // kiếm hiện tại, nên không gọi focusPoi được (nó tra cứu theo
                  // danh sách đó). Bay bản đồ bằng chính toạ độ panel đang giữ
                  // và để hook nạp phần còn lại.
                  const target = poiDetail?.similar.find(
                    (item) => item.id === poiId,
                  );
                  openDetail(poiId, 'similar');
                  if (target) {
                    mapRef.current?.flyTo({
                      center: [target.longitude, target.latitude],
                      zoom: 15.5,
                      essential: true,
                    });
                  } else {
                    flyToOnDetailRef.current = poiId;
                  }
                }}
                onReviewSubmitted={({ ratingMean, ratingCount }) => {
                  refreshPoiDetail();
                  const updateRating = (items: Poi[]) =>
                    items.map((item) =>
                      item.id === detailPoiId
                        ? {
                            ...item,
                            rating: ratingMean,
                            reviewCount: ratingCount,
                          }
                        : item,
                    );
                  setPois(updateRating);
                  setAreaPois(updateRating);
                  setStatus('Đánh giá của bạn đã được lưu');
                }}
              />
            </div>
          )}
        </section>
      </section>
    </main>
  );
}
