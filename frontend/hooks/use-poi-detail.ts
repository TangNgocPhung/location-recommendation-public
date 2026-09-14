'use client';

import { useCallback, useEffect, useState } from 'react';

import type { PoiDetail, PoiPhotos } from '@/components/poi-detail-panel';

type Position = { latitude: number; longitude: number };

// Cùng hằng với location-explorer.tsx: POI thật mang UUID từ database, POI mẫu
// hard-code mang id dạng 'poi-001'. Gọi /api/v1/pois/poi-001 chỉ nhận về 400
// "poi_id phải là UUID" — một vệt đỏ trong console và một thẻ báo lỗi đập vào
// mặt người dùng, đổi lấy một câu trả lời đã biết trước.
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function readErrorDetail(response: Response): Promise<string> {
  // Backend trả lỗi bằng JSONResponse với khoá "detail" tiếng Việt. Hiện đúng
  // câu đó ("Không có địa điểm này") thay vì "HTTP 404": mã số không nói cho
  // người dùng biết họ nên làm gì tiếp.
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string' && body.detail.length > 0)
      return body.detail;
  } catch {
    // Thân phản hồi không phải JSON (proxy chặn, gateway chết trước khi tới
    // FastAPI). Rơi về mã HTTP còn hơn là nuốt lỗi.
  }
  return `Máy chủ trả lỗi ${response.status}`;
}

/**
 * Nạp dữ liệu trang chi tiết cho một POI.
 *
 * Hai yêu cầu bay SONG SONG chứ không nối đuôi: `/api/v1/pois/{id}` chỉ đọc
 * Postgres nên về gần như tức thì, còn `/api/v1/pois/{id}/photos` có thể phải
 * hỏi Wikimedia (bắt giãn nhịp ≥ 1 giây giữa hai request, nên lần dò nguội mất
 * vài giây). Chờ cả hai rồi mới vẽ là bắt người dùng nhìn khung xám vài giây
 * cho một thứ đã sẵn sàng — Google Maps hiện chữ trước, ảnh lấp vào sau, và
 * đây làm đúng như vậy.
 *
 * `loading` bật khi CÒN BẤT KỲ yêu cầu nào đang bay, vì panel dùng nó cho hai
 * việc: khung xương toàn trang (chỉ khi chưa có `detail`) và khung xương riêng
 * ô ảnh (`loading && !photos`). Nếu tắt `loading` ngay lúc chi tiết về thì
 * trong lúc ảnh còn đang bay panel sẽ khẳng định "Chưa dò được ảnh" — nói sai
 * về dữ liệu, đúng thứ tuyệt đối không được phép ở đồ án này.
 *
 * `error` CHỈ phản ánh yêu cầu chi tiết. Ảnh hỏng không được chiếm cả panel:
 * nó rơi về `status: 'unavailable'`, để panel nói "chưa dò được ảnh" thay vì
 * xoá sạch thông tin địa điểm vốn đã lấy được.
 */
export function usePoiDetail(
  apiBaseUrl: string,
  poiId: string | null,
  position: Position | null,
) {
  const [detail, setDetail] = useState<PoiDetail | null>(null);
  const [photos, setPhotos] = useState<PoiPhotos | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [photosLoading, setPhotosLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailRevision, setDetailRevision] = useState(0);
  const refreshDetail = useCallback(
    () => setDetailRevision((value) => value + 1),
    [],
  );

  // Tách lat/lng thành số nguyên thuỷ. Trang cha dựng một object `position` mới
  // sau mỗi ping GPS; phụ thuộc vào chính object thì effect chạy lại ngay cả
  // khi hai toạ độ giống hệt nhau.
  //
  // `null` ở đây nghĩa là CHƯA có vị trí thật của người dùng — khác hẳn toạ độ
  // mặc định ở trung tâm thành phố mà trang cha dùng để mồi bản đồ.
  const latitude = position?.latitude ?? null;
  const longitude = position?.longitude ?? null;

  // Chi tiết — phụ thuộc cả vị trí, vì distanceMeters/etaMinutes tính từ đó.
  //
  // AbortController huỷ yêu cầu cũ mỗi lần đổi POI. Bấm nhanh ba POI liên tiếp
  // là ba yêu cầu cùng bay, và nếu không huỷ thì cái nào về SAU sẽ ghi đè —
  // người dùng đang xem quán C lại thấy thông tin quán A mình đã bỏ chọn. Lỗi
  // này đã cắn một lần ở effect lấy tuyến đường trong location-explorer.tsx;
  // nó chỉ lộ ra khi mạng chậm nên rất khó lần ra.
  useEffect(() => {
    if (!poiId || !UUID_PATTERN.test(poiId)) {
      setDetail(null);
      setDetailLoading(false);
      setError(null);
      return;
    }

    // Chỉ xoá khi đây là một POI KHÁC. Effect còn chạy lại mỗi khi GPS nhích đủ
    // xa; xoá vô điều kiện thì panel chớp về khung xương giữa lúc người dùng
    // đang đọc, cứ 15 giây một lần.
    setDetail((current) => (current && current.id === poiId ? current : null));
    setError(null);
    setDetailLoading(true);

    const controller = new AbortController();
    void (async () => {
      try {
        // Chưa có vị trí thì KHÔNG gửi lat/lng: backend trả distanceMeters và
        // etaMinutes bằng null, panel nói "chưa biết khoảng cách". Gửi bừa toạ
        // độ mặc định là in ra một con số đo từ chỗ không ai đứng, trình bày y
        // như khoảng cách thật — đúng kiểu dối số liệu mà đồ án này cấm.
        const url =
          latitude !== null && longitude !== null
            ? `${apiBaseUrl}/api/v1/pois/${poiId}` +
              `?lat=${encodeURIComponent(latitude)}&lng=${encodeURIComponent(longitude)}`
            : `${apiBaseUrl}/api/v1/pois/${poiId}`;
        const response = await fetch(url, { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (!response.ok) {
          const message = await readErrorDetail(response);
          if (controller.signal.aborted) return;
          setDetail(null);
          setError(message);
          return;
        }
        const data = (await response.json()) as PoiDetail;
        if (controller.signal.aborted) return;
        setDetail(data);
      } catch (caught) {
        if ((caught as Error)?.name === 'AbortError') return;
        setDetail(null);
        setError('Không kết nối được tới máy chủ');
      } finally {
        // Không tắt cờ cho một yêu cầu đã bị huỷ: effect kế tiếp đã bật nó lên
        // cho POI mới, tắt ở đây là làm panel nhấp nháy hết khung xương.
        if (!controller.signal.aborted) setDetailLoading(false);
      }
    })();

    return () => controller.abort();
  }, [apiBaseUrl, poiId, latitude, longitude, detailRevision]);

  // Ảnh — CỐ TÌNH không phụ thuộc vị trí. Trang cha gọi setPosition sau mỗi ping
  // GPS đủ điều kiện (≥ 50 m hoặc ≥ 15 giây), mà ảnh của một địa điểm thì không
  // đổi theo chỗ người xem đứng. Gộp chung vào effect trên là cứ 15 giây lại
  // nện Wikimedia một lần cho cùng một POI — đúng kiểu lưu lượng ăn HTTP 429.
  useEffect(() => {
    if (!poiId || !UUID_PATTERN.test(poiId)) {
      setPhotos(null);
      setPhotosLoading(false);
      return;
    }

    setPhotos(null);
    setPhotosLoading(true);

    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(
          `${apiBaseUrl}/api/v1/pois/${poiId}/photos?limit=8`,
          {
            signal: controller.signal,
          },
        );
        if (controller.signal.aborted) return;
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = (await response.json()) as PoiPhotos;
        if (controller.signal.aborted) return;
        setPhotos(data);
      } catch (caught) {
        if ((caught as Error)?.name === 'AbortError') return;
        // 'unavailable' chứ KHÔNG phải mảng rỗng: mảng rỗng nghĩa là "đã dò và
        // quanh đây thật sự không có ảnh nào", còn đây mới chỉ là "chưa hỏi
        // được". Biến một lần rớt mạng thành lời khẳng định về dữ liệu là dối.
        setPhotos({
          poiId,
          status: 'unavailable',
          fetchedAt: null,
          photos: [],
        });
      } finally {
        if (!controller.signal.aborted) setPhotosLoading(false);
      }
    })();

    return () => controller.abort();
  }, [apiBaseUrl, poiId]);

  return {
    detail,
    photos,
    loading: detailLoading || photosLoading,
    error,
    refreshDetail,
  };
}
