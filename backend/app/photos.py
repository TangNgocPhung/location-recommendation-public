"""Ảnh cho trang chi tiết địa điểm — nguồn duy nhất là Wikimedia Commons.

OpenStreetMap không có trường ảnh. Đếm THẺ trên 3.000 bản ghi OSM đã nhập được
2 `image`, 4 `wikimedia_commons`, 5 `wikidata` — nhưng đếm theo POI thì chỉ có
**7 POI trên 3.010** mang ít nhất một trong ba thẻ đó (nhiều POI mang cùng lúc
`wikimedia_commons` và `wikidata`). Trong 7 POI ấy, 2 POI có thẻ `image` trỏ
tới Google Sites và gstatic.com nên bị `_ALLOWED_IMAGE_HOSTS` từ chối.

Tức **5 POI, 0,17%, thật sự lấy được ảnh của chính nó.** Mọi thứ còn lại, nếu
có ảnh, là ảnh chụp quanh đó do người khác tải lên Commons.

Đây là toàn bộ lý do tồn tại của trường ``confidence``:

- ``place`` — ảnh CỦA địa điểm, suy từ thẻ OSM của chính POI đó.
- ``area``  — ảnh chụp QUANH ĐÓ, tìm bằng ``list=geosearch`` theo toạ độ. Thử
  25 POI thì 24 có ảnh trong bán kính 120-300 m, nhưng phần lớn là ảnh con
  phố, ảnh ô tô, ảnh logo. Giao diện BẮT BUỘC ghi nhãn "Ảnh khu vực · cách N m"
  cho loại này. Một tấm ảnh không nhãn là một lời nói dối im lặng, và đây là
  đồ án sẽ bị hội đồng chất vấn.

Ba ràng buộc kỹ thuật đã trả giá để biết:

**Giãn nhịp gọi.** Commons API gọi được từ trong container backend (HTTP 200),
nhưng gọi dồn dập trả HTTP 429. Mọi request đi qua ``_throttle()`` — cách nhau
tối thiểu một giây trên toàn tiến trình.

**User-Agent phải mô tả rõ ứng dụng.** Wikimedia chặn User-Agent mặc định của
thư viện. Chuỗi ở ``USER_AGENT`` là định danh của DỰ ÁN; tuyệt đối không nhét
email hay bất kỳ thông tin cá nhân nào của người dùng vào đây — mọi request ra
ngoài đều là một lần rò rỉ tiềm năng.

**Lỗi thì trả rỗng, không ném.** Cùng nguyên tắc với ``app.directions``: một
quán không có ảnh — hay một lần mất mạng — không được phép làm hỏng cả trang
chi tiết. Nhưng "không có ảnh" và "chưa dò được" là hai kết luận KHÁC NHAU và
được trả về khác nhau (``empty`` so với ``unavailable``); chỉ ``empty`` mới
được ghi vào cache.
"""

from __future__ import annotations

import html
import json
import logging
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import settings

logger = logging.getLogger("nearby-photos")

DATABASE_URL = settings.database_url

COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"

# Định danh của DỰ ÁN, không phải của người dùng. Xem chú thích đầu file.
USER_AGENT = "NearbyPOI/0.1 (https://github.com/TangNgocPhung/location-recommendation)"

# Khoảng cách tối thiểu giữa hai lần gọi Wikimedia. Đo thật: gọi liên tiếp
# không giãn nhịp thì tới lần thứ ba đã nhận HTTP 429.
MIN_SECONDS_BETWEEN_CALLS = 1.0
MAX_ATTEMPTS = 3
RETRY_STATUS_CODES = (429, 503)
RETRY_BASE_DELAY_SECONDS = 1.0

MAX_PHOTOS = 8
# Bán kính geosearch. 300 m là ngưỡng đo được: rộng hơn thì ảnh "khu vực" xa
# tới mức nhãn khoảng cách cũng không cứu nổi, hẹp hơn thì gần như luôn rỗng.
AREA_RADIUS_METERS = 300
GEOSEARCH_LIMIT = 20
# Bề rộng thumbnail nhờ Wikimedia render sẵn. Tự co ảnh gốc ở phía trình duyệt
# nghĩa là tải về ảnh 4000px cho một ô 300px.
THUMB_WIDTH = 800
# Trần số file trong một lần gọi prop=imageinfo (giới hạn của API cho client
# không đăng nhập là 50).
FILE_DETAILS_BATCH = 50

_throttle_lock = threading.Lock()
_last_call_at = 0.0

# Trần số luồng được phép nằm trong đường dò Wikimedia CÙNG LÚC.
#
# Endpoint ảnh là `def` nên FastAPI chạy nó trong threadpool của anyio — mặc
# định 40 slot dùng CHUNG cho mọi endpoint, vì trong api.py endpoint nào cũng là
# `def`. Một lần dò nguội giữ slot ít nhất hai giây (geosearch + imageinfo, cách
# nhau một giây vì _throttle), và lâu hơn nhiều khi mạng hỏng. Không chặn ở đây
# thì vài chục request ảnh cho POI chưa cache là đủ làm 40 slot kẹt hết trong
# time.sleep, và CẢ API — /health, /api/pois/nearby, /api/v1/search — ngừng trả
# lời. Rate limit ở api.py không cứu được: khoá của nó là X-Session-ID do chính
# client tự khai, đổi UUID là có hạn mức mới.
#
# 2 là đủ: _throttle() vốn đã ép cả tiến trình xuống một request mỗi giây, nên
# luồng thứ ba chỉ đứng giữ slot chứ không làm ảnh về nhanh hơn một giây nào.
MAX_CONCURRENT_FETCHES = 2
# Chờ ngắn rồi bỏ cuộc chứ KHÔNG chờ vô hạn: mục đích là nhả slot threadpool cho
# endpoint khác; chờ lâu thì slot vẫn bị giữ y như cũ, chẳng khác gì không sửa.
FETCH_SLOT_WAIT_SECONDS = 0.5
_fetch_slots = threading.BoundedSemaphore(MAX_CONCURRENT_FETCHES)

# Hạn chót của riêng luồng đang chạy, đặt ở `fetch_and_store` và đọc ở
# `_throttle`/`commons_api`. Dùng threading.local() để không phải đổi chữ ký của
# cả chuỗi hàm ở giữa.
#
# BẮT BUỘC xoá ở `finally`: luồng threadpool được DÙNG LẠI cho request sau, không
# xoá thì request kế thừa hạn chót đã hết của request trước và không gọi nổi lần
# nào — im lặng trả 'unavailable' mãi mãi.
_deadline = threading.local()


def _throttle() -> bool:
    """Giữ nhịp gọi Wikimedia ở mức tối đa một request mỗi giây.

    CỐ Ý ngủ trong lúc đang giữ khoá: mục tiêu là giới hạn nhịp của cả tiến
    trình, không phải của từng luồng. Thả khoá rồi mới ngủ thì mười luồng cùng
    thấy "đã đủ một giây" và bắn ra mười request cùng lúc — đúng kiểu gọi dồn
    dập đã ăn 429.

    Trả ``False`` khi đã hết hạn chót của lần dò. Hạn chót phải kiểm NGAY TẠI
    `acquire()` chứ không chỉ quanh lần gọi mạng: phần chờ dài nhất của một
    request nguội là chờ TRƯỚC khoá, mười luồng xếp hàng thì luồng thứ mười chờ
    mười giây mà vẫn chiếm một slot trong threadpool dùng chung với /search,
    /directions, /geofences.
    """
    global _last_call_at
    at = getattr(_deadline, "at", None)
    if at is None:
        _throttle_lock.acquire()
    else:
        remaining = at - time.monotonic()
        # Hết giờ rồi thì đừng giành khoá nữa: giành được cũng chỉ để ngủ thêm
        # một giây trong lúc giữ khoá, làm chậm cả những luồng còn kịp.
        if remaining <= 0 or not _throttle_lock.acquire(timeout=remaining):
            return False
    try:
        waiting = MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - _last_call_at)
        if waiting > 0:
            time.sleep(waiting)
        _last_call_at = time.monotonic()
    finally:
        _throttle_lock.release()
    return at is None or time.monotonic() < at


def commons_api(
    params: dict[str, Any],
    endpoint: str = COMMONS_API_URL,
    probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Một lần gọi MediaWiki API. Trả ``{}`` khi không lấy được gì.

    ``probe`` là cách phân biệt "API trả lời rằng không có gì" với "không gọi
    được API". Hai thứ đó nhìn từ ngoài giống hệt nhau — cùng là danh sách rỗng
    — nhưng một cái được phép ghi vào cache còn cái kia thì không. Hàm gọi
    truyền vào một dict và đọc hai cờ sau cùng: ``probe["ok"]`` = có ít nhất
    một request thành công, ``probe["failed"]`` = có ít nhất một request hỏng.

    Phải có ĐỦ CẢ HAI cờ, không được chỉ có ``ok``. Một lần dò gọi nhiều
    request nối tiếp (geosearch → imageinfo), mà ``ok`` thì chỉ bật lên chứ
    không bao giờ tắt: nếu geosearch xong rồi imageinfo mới rớt, chỉ nhìn
    ``ok`` sẽ thấy True và kết luận "quanh đây không có ảnh" — rồi ghi kết
    luận sai đó vào cache suốt ``photo_cache_days`` ngày.
    """
    query = {"format": "json", "formatversion": 2, **params}
    url = f"{endpoint}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )

    delay = RETRY_BASE_DELAY_SECONDS
    for attempt in range(1, MAX_ATTEMPTS + 1):
        # Hết hạn chót thì bỏ cuộc: rơi xuống nhánh probe["failed"] ở cuối hàm,
        # tức kết quả là "chưa dò được", không phải "không có ảnh".
        if not _throttle():
            break
        at = getattr(_deadline, "at", None)
        timeout = settings.wikimedia_timeout_seconds
        if at is not None:
            timeout = min(timeout, max(0.1, at - time.monotonic()))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            # 429/503 là "thử lại sau", không phải "không có dữ liệu". Backoff
            # nhân đôi cộng với _throttle() ở vòng sau.
            if error.code in RETRY_STATUS_CODES and attempt < MAX_ATTEMPTS:
                # Chỉ backoff khi còn kịp thử lại: ngủ hết ngân sách rồi mới gọi
                # là giữ luồng thêm mấy giây để chắc chắn nhận về tay trắng.
                if at is not None and time.monotonic() + delay >= at:
                    break
                logger.debug("Wikimedia trả %s, thử lại sau %.1fs", error.code, delay)
                time.sleep(delay)
                delay *= 2
                continue
            logger.debug("Wikimedia trả HTTP %s cho %s", error.code, params.get("action"))
            break
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as error:
            # Mức debug: chạy offline là chuyện thường và ảnh chỉ là phần thêm.
            logger.debug("Không gọi được Wikimedia: %s", error)
            break

        if isinstance(payload, dict):
            if probe is not None:
                probe["ok"] = True
            return payload
        break

    if probe is not None:
        probe.setdefault("ok", False)
        probe["failed"] = True
    return {}


@dataclass(frozen=True)
class Candidate:
    """Một ứng viên ảnh, trước khi hỏi Wikimedia về kích thước/giấy phép."""

    title: str  # luôn ở dạng 'File:....jpg'
    confidence: str  # 'place' | 'area'
    dist: float | None = None  # khoảng cách (m) do geosearch trả về


# --- Bộ lọc theo tên file -----------------------------------------------------

# Đuôi file hiển thị được trong thẻ <img>. Danh sách TRẮNG chứ không phải danh
# sách đen: nó loại luôn .svg, .pdf, .ogv, .webm, .tif và mọi định dạng lạ khác
# mà không cần liệt kê hết.
_IMAGE_EXTENSION_RE = re.compile(r"\.(jpe?g|png|gif|webp)$", re.IGNORECASE)

# Những từ trong tên file gần như chắc chắn báo hiệu "đây không phải ảnh một
# địa điểm". So khớp theo TỪ (tên file đã được chuẩn hóa thành chuỗi các từ
# cách nhau bởi dấu cách) chứ không theo chuỗi con, nếu không thì "Mapo" hay
# "Flagship" cũng bị loại oan.
#
# QUAN TRỌNG — bộ lọc này CHỈ GIẢM RÁC. Nó không, và không thể, biến một tấm
# ảnh khu vực thành ảnh của quán: một tấm ảnh con phố có tên
# "Nguyen Hue Boulevard 2019.jpg" vượt qua bộ lọc này dễ dàng. Cái quyết định
# sự trung thực là nhãn `confidence`, không phải regex này.
_NON_PHOTO_TITLE_RE = re.compile(
    r"\s(logos?|icons?|maps?|mapa|carte|karte|diagram|diagramme|chart|graph|seal|"
    r"coat of arms|flag|banner|poster|screenshot|qr|qr code)\s"
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Chỉ nhận ảnh từ hạ tầng Wikimedia. Thẻ `image` của OSM là do người dùng gõ
# tay và có thể trỏ tới bất kỳ máy chủ nào; nhúng thẳng URL lạ vào trang là
# giao quyền quyết định nội dung hiển thị cho một bên thứ ba tuỳ ý, đồng thời
# làm rò referrer của người dùng sang đó.
_ALLOWED_IMAGE_HOSTS = frozenset({"upload.wikimedia.org", "commons.wikimedia.org"})


def is_displayable_image(title: str) -> bool:
    """Tên file có đuôi mà trình duyệt hiển thị được trong thẻ ``<img>``."""
    return bool(title) and _IMAGE_EXTENSION_RE.search(title) is not None


def is_plausible_place_photo(title: str) -> bool:
    """Có đáng coi là ảnh của một địa điểm không (lọc thô theo tên file).

    Áp cho ứng viên ``area``, nơi geosearch trả về đủ thứ: bản đồ, huy hiệu,
    ảnh chụp màn hình, logo thương hiệu. Ứng viên ``place`` chỉ bị kiểm đuôi
    file — nếu chính thẻ OSM của địa điểm trỏ tới tấm ảnh đó thì đấy là ảnh mà
    chủ dữ liệu tự khai, ta không có tư cách đoán lại bằng tên file.
    """
    if not is_displayable_image(title):
        return False
    name = title.split(":", 1)[-1]
    normalized = " " + re.sub(r"[^a-z0-9]+", " ", name.lower()).strip() + " "
    return _NON_PHOTO_TITLE_RE.search(normalized) is None


def _plain_text(value: Any, limit: int = 200) -> str | None:
    """Bóc thẻ HTML và giải mã entity.

    ``extmetadata.Artist`` của Wikimedia trả về **HTML** (thường là một thẻ
    ``<a>`` trỏ tới trang người dùng). Nhét thẳng vào giao diện thì vừa xấu vừa
    là lỗ XSS nếu phía React dùng ``dangerouslySetInnerHTML`` ở đâu đó. Bóc tại
    đây — chỗ dữ liệu vào hệ thống — chứ không trông chờ mọi nơi hiển thị đều
    nhớ bóc.

    Thứ tự hai lời gọi là phần dễ làm sai nhất: giải mã entity TRƯỚC rồi mới bóc
    thẻ. Làm ngược lại thì bước bóc không nhìn thấy phần đã mã hoá, và bước
    unescape dựng '&lt;img src=x onerror=...&gt;' thành thẻ SỐNG — hàm lọc hoá
    ra lại là hàm sinh HTML. Và chỉ unescape ĐÚNG MỘT lần: lặp cho tới khi không
    đổi nữa là mở lại đúng cái lỗ vừa bịt.
    """
    if not value:
        return None
    text = _HTML_TAG_RE.sub(" ", html.unescape(str(value)))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or None


# Những giấy phép KHÔNG đòi ghi tên tác giả. CC BY và CC BY-SA — phần lớn ảnh
# Commons — thì ĐÒI, nên thiếu tác giả là dùng sai giấy phép chứ không phải
# thiếu một dòng trang trí.
#
# Khớp theo ĐẦU chuỗi `LicenseShortName`: 'CC0 1.0', 'Public domain',
# 'PD-US-expired', 'PD-old-70'. `\b` sau 'pd' để 'PDM' (Public Domain Mark, một
# nhãn khác) không lọt vào đây. Nghi ngờ thì coi như phải ghi công — sai theo
# hướng mất một tấm ảnh còn hơn sai theo hướng vi phạm giấy phép.
_NO_ATTRIBUTION_LICENSE_RE = re.compile(r"^(cc0|public domain|pd)\b", re.IGNORECASE)


def _requires_attribution(license_name: str) -> bool:
    """Giấy phép này có bắt buộc ghi tên tác giả không."""
    return _NO_ATTRIBUTION_LICENSE_RE.match(license_name.strip()) is None


def _normalize_title(value: str | None) -> str | None:
    """Đưa mọi cách viết của người nhập OSM về dạng 'File:x.jpg' / 'Category:y'."""
    if not value:
        return None
    text = urllib.parse.unquote(str(value).strip()).replace("_", " ").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("commons:"):
        text = text.split(":", 1)[1].strip()
        lowered = text.lower()
    if lowered.startswith("category:"):
        return "Category:" + text.split(":", 1)[1].strip()
    # 'Image:' là tên gọi cũ của không gian tên File; 'Tập tin:'/'Tệp:' là bản
    # tiếng Việt — người nhập ở TP.HCM dùng cả ba.
    for prefix in ("file:", "image:", "tập tin:", "tệp:"):
        if lowered.startswith(prefix):
            return "File:" + text.split(":", 1)[1].strip()
    return "File:" + text


def _title_from_url(value: str) -> str | None:
    """Rút tên file Commons từ một URL trong thẻ ``image``.

    Luôn quy về tên file thay vì dùng thẳng URL: chỉ khi có tên file mới hỏi
    được giấy phép và tác giả, mà ảnh Commons KHÔNG được hiển thị thiếu ghi
    công. Không rút được tên thì bỏ ứng viên, chứ không hiển thị ảnh trần.
    """
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() not in _ALLOWED_IMAGE_HOSTS:
        return None
    path = urllib.parse.unquote(parsed.path)
    if "/wiki/" in path:
        return _normalize_title(path.split("/wiki/", 1)[1])
    name = path.rsplit("/", 1)[-1]
    if "/thumb/" in path:
        # .../thumb/a/ab/Foo.jpg/800px-Foo.jpg → Foo.jpg
        match = re.match(r"^\d+px-(.+)$", name)
        if match:
            name = match.group(1)
    return _normalize_title(name)


# --- Ứng viên ------------------------------------------------------------------


def _category_files(title: str, probe: dict[str, Any] | None = None) -> list[str]:
    payload = commons_api(
        {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": title,
            "cmtype": "file",
            "cmlimit": MAX_PHOTOS * 2,
        },
        probe=probe,
    )
    members = (payload.get("query") or {}).get("categorymembers") or []
    return [member.get("title") for member in members if member.get("title")]


def _wikidata_image(qid: str, probe: dict[str, Any] | None = None) -> str | None:
    """Ảnh chính (P18) của một thực thể Wikidata."""
    payload = commons_api(
        {"action": "wbgetclaims", "entity": qid, "property": "P18"},
        endpoint=WIKIDATA_API_URL,
        probe=probe,
    )
    for claim in (payload.get("claims") or {}).get("P18") or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, str) and value.strip():
            return _normalize_title(value)
    return None


def photos_from_tags(
    tags: dict[str, Any] | None, probe: dict[str, Any] | None = None
) -> list[Candidate]:
    """Ứng viên ``place``: ảnh mà chính thẻ OSM của địa điểm trỏ tới."""
    if not isinstance(tags, dict) or not tags:
        return []

    titles: list[str] = []

    def add(title: str | None) -> None:
        if title and title not in titles:
            titles.append(title)

    raw_image = tags.get("image")
    if isinstance(raw_image, str) and raw_image.strip():
        value = raw_image.strip()
        # Một tên file trên Commons gần như không bao giờ chứa '/'. Mọi giá trị
        # có '/' đều coi là URL và bắt đi qua chốt chặn host, KỂ CẢ dạng thiếu
        # scheme mà người nhập OSM hay viết: '//host/x.jpg', 'www.host/x.jpg'.
        # Trước đây chỉ bắt theo '://', nên hai dạng kia rơi thẳng vào
        # _normalize_title và thành 'File:www.host/x.jpg' — một tên vô nghĩa,
        # tốn một lượt gọi Wikimedia cộng một giây _throttle() chỉ để nhận lại
        # 'missing'. Không khai thác được (URL cuối cùng luôn do imageinfo trả
        # về) nhưng là lãng phí thuần tuý trên một API có rate limit.
        add(_title_from_url(value) if "/" in value else _normalize_title(value))

    raw_commons = tags.get("wikimedia_commons")
    if isinstance(raw_commons, str) and raw_commons.strip():
        title = _normalize_title(raw_commons)
        if title and title.startswith("Category:"):
            for member in _category_files(title, probe):
                add(member)
        else:
            add(title)

    raw_qid = tags.get("wikidata")
    if isinstance(raw_qid, str) and re.fullmatch(r"Q\d+", raw_qid.strip()):
        add(_wikidata_image(raw_qid.strip(), probe))

    return [
        Candidate(title=title, confidence="place")
        for title in titles
        if is_displayable_image(title)
    ]


def photos_near(
    lat: float,
    lng: float,
    radius_m: int = AREA_RADIUS_METERS,
    probe: dict[str, Any] | None = None,
) -> list[Candidate]:
    """Ứng viên ``area``: ảnh có toạ độ nằm quanh địa điểm.

    ``gsnamespace=6`` giới hạn ở không gian tên File — thiếu nó thì geosearch
    trả về cả bài viết, và tên bài viết không phải tên file.
    """
    payload = commons_api(
        {
            "action": "query",
            "list": "geosearch",
            "gscoord": f"{float(lat):.6f}|{float(lng):.6f}",
            # API chỉ nhận bán kính 10-10.000 m; kẹp để không bị từ chối cả lệnh.
            "gsradius": max(10, min(10_000, int(radius_m))),
            "gslimit": GEOSEARCH_LIMIT,
            "gsnamespace": 6,
        },
        probe=probe,
    )
    results: list[Candidate] = []
    for item in (payload.get("query") or {}).get("geosearch") or []:
        title = item.get("title")
        if not title or not is_plausible_place_photo(title):
            continue
        try:
            distance = float(item.get("dist"))
        except (TypeError, ValueError):
            distance = None
        results.append(Candidate(title=title, confidence="area", dist=distance))
    results.sort(key=lambda candidate: candidate.dist if candidate.dist is not None else math.inf)
    return results


def file_details(
    titles: list[str], probe: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """URL, kích thước, giấy phép và tác giả cho NHIỀU file trong ít lần gọi.

    Gộp tối đa ``FILE_DETAILS_BATCH`` file mỗi request. Gọi từng file một thì
    tám tấm ảnh là tám giây chờ vì ``_throttle()`` — người dùng bỏ trang trước
    khi ảnh kịp về.
    """
    details: dict[str, dict[str, Any]] = {}
    unique = [title for index, title in enumerate(titles) if title and title not in titles[:index]]

    for start in range(0, len(unique), FILE_DETAILS_BATCH):
        chunk = unique[start : start + FILE_DETAILS_BATCH]
        payload = commons_api(
            {
                "action": "query",
                "titles": "|".join(chunk),
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|size",
                # Nhờ Wikimedia render sẵn bản thu nhỏ; `thumburl` chỉ xuất hiện
                # khi có tham số này.
                "iiurlwidth": THUMB_WIDTH,
            },
            probe=probe,
        )
        for page in (payload.get("query") or {}).get("pages") or []:
            if page.get("missing") or not page.get("title"):
                continue
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            url = info.get("url")
            if not url:
                continue
            extmetadata = info.get("extmetadata") or {}
            details[page["title"]] = {
                "url": url,
                "thumbUrl": info.get("thumburl") or url,
                "width": info.get("thumbwidth") or info.get("width"),
                "height": info.get("thumbheight") or info.get("height"),
                "license": _plain_text((extmetadata.get("LicenseShortName") or {}).get("value")),
                "attribution": _plain_text((extmetadata.get("Artist") or {}).get("value")),
                "sourceUrl": info.get("descriptionurl")
                or "https://commons.wikimedia.org/wiki/"
                + urllib.parse.quote(page["title"].replace(" ", "_")),
            }
    return details


# --- Cache Postgres ------------------------------------------------------------


def _row_to_photo(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "url": row["url"],
        "thumbUrl": row["thumb_url"],
        "width": row["width"],
        "height": row["height"],
        "title": row["title"],
        "confidence": row["confidence"],
        "distanceMeters": row["distance_meters"],
        "source": row["source"],
        "sourceUrl": row["source_url"],
        "license": row["license"],
        "attribution": row["attribution"],
    }


def cached_photos(
    poi_id: str, limit: int = MAX_PHOTOS, database_url: str | None = None
) -> dict[str, Any] | None:
    """Kết quả đã dò trước đó. ``None`` nghĩa là CHƯA dò (hoặc đã quá hạn).

    Phân biệt ``None`` với ``status='empty'`` là cả ý nghĩa của bảng
    ``poi_photo_fetches``: ``empty`` là một câu trả lời đã kiểm chứng ("đã hỏi
    Wikimedia, quanh đây không có ảnh nào"), còn ``None`` là chưa hỏi.
    """
    with psycopg.connect(database_url or DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT fetched_at, status FROM poi_photo_fetches WHERE poi_id = %s",
                (poi_id,),
            )
            fetch = cursor.fetchone()
            if fetch is None:
                return None
            # Quá hạn thì coi như chưa dò: Commons liên tục có ảnh mới, và một
            # quán hôm nay chưa có ảnh tháng sau có thể đã có.
            expiry = datetime.now(timezone.utc) - timedelta(days=settings.photo_cache_days)
            if fetch["fetched_at"] < expiry:
                return None

            cursor.execute(
                """
                SELECT id, url, thumb_url, width, height, title, confidence,
                       distance_meters, source, source_url, license, attribution
                FROM poi_photos
                WHERE poi_id = %s
                ORDER BY position ASC, id ASC
                LIMIT %s
                """,
                (poi_id, limit),
            )
            photos = [_row_to_photo(row) for row in cursor.fetchall()]

    return {
        "poiId": poi_id,
        "status": fetch["status"],
        "fetchedAt": fetch["fetched_at"].isoformat(),
        "photos": photos,
    }


def _store(
    poi_id: str, rows: list[dict[str, Any]], status: str, database_url: str | None = None
) -> None:
    with psycopg.connect(database_url or DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            # Xoá rồi ghi lại thay vì upsert từng dòng: lần dò sau có thể trả về
            # ít ảnh hơn (file bị xoá khỏi Commons), và giữ lại dòng cũ nghĩa là
            # hiển thị một URL đã chết.
            cursor.execute("DELETE FROM poi_photos WHERE poi_id = %s", (poi_id,))
            for position, row in enumerate(rows):
                cursor.execute(
                    """
                    INSERT INTO poi_photos (
                        poi_id, source, source_ref, url, thumb_url, width, height,
                        title, confidence, distance_meters, license, attribution,
                        source_url, position
                    ) VALUES (
                        %(poi_id)s, %(source)s, %(source_ref)s, %(url)s, %(thumb_url)s,
                        %(width)s, %(height)s, %(title)s, %(confidence)s,
                        %(distance_meters)s, %(license)s, %(attribution)s,
                        %(source_url)s, %(position)s
                    )
                    ON CONFLICT (poi_id, source, source_ref) DO NOTHING
                    """,
                    {**row, "poi_id": poi_id, "position": position},
                )
            cursor.execute(
                """
                INSERT INTO poi_photo_fetches (poi_id, fetched_at, photo_count, status)
                VALUES (%s, NOW(), %s, %s)
                ON CONFLICT (poi_id) DO UPDATE
                    SET fetched_at = NOW(),
                        photo_count = EXCLUDED.photo_count,
                        status = EXCLUDED.status
                """,
                (poi_id, len(rows), status),
            )
        connection.commit()


def fetch_and_store(
    poi_id: str,
    lat: float | None,
    lng: float | None,
    tags: dict[str, Any] | None,
    limit: int = MAX_PHOTOS,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Dò Wikimedia cho một POI, nhưng không bao giờ chiếm quá vài slot threadpool.

    Hai lớp chặn, bổ sung cho nhau vì chúng chặn hai thứ khác nhau:

    - `_fetch_slots` giới hạn SỐ luồng cùng nằm trong đường dò. Vượt trần thì
      trả ngay chứ không xếp hàng — xếp hàng vẫn là giữ slot threadpool, đúng
      thứ cần nhả ra.
    - `_deadline` giới hạn THỜI GIAN một lượt dò được phép chạy, kể cả phần chờ
      trước khoá giãn nhịp. Không có nó thì một lượt dò xấu giữ slot tới gần
      một phút rưỡi dù chỉ có một người dùng.

    Cả hai đường bỏ cuộc đều trả 'unavailable' — đúng nghĩa đã định ở đầu file:
    CHƯA dò được, KHÔNG phải "địa điểm này không có ảnh" — và giống nhánh probe
    hỏng, chúng không ghi gì vào cache nên lần sau mở lại là dò lại.
    """
    if not _fetch_slots.acquire(timeout=FETCH_SLOT_WAIT_SECONDS):
        logger.debug(
            "Bỏ qua dò ảnh cho %s: đang có %d lượt dò chạy", poi_id, MAX_CONCURRENT_FETCHES
        )
        return {"poiId": poi_id, "status": "unavailable", "fetchedAt": None, "photos": []}
    _deadline.at = time.monotonic() + settings.photo_fetch_budget_seconds
    try:
        return _fetch_and_store(poi_id, lat, lng, tags, limit, database_url)
    finally:
        # Xoá hạn chót TRƯỚC khi nhả slot, và luôn xoá: luồng threadpool được
        # dùng lại cho request sau.
        _deadline.at = None
        _fetch_slots.release()


def _fetch_and_store(
    poi_id: str,
    lat: float | None,
    lng: float | None,
    tags: dict[str, Any] | None,
    limit: int = MAX_PHOTOS,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Dò Wikimedia cho một POI, ghi cache, trả về đúng dạng hợp đồng ảnh."""
    probe: dict[str, Any] = {}

    candidates = photos_from_tags(tags, probe)
    # Chỉ đi tìm ảnh khu vực khi ảnh của chính địa điểm chưa đủ. Có ảnh thật
    # rồi thì độn thêm ảnh con phố chỉ làm loãng.
    if len(candidates) < MAX_PHOTOS and lat is not None and lng is not None:
        candidates = candidates + photos_near(float(lat), float(lng), AREA_RADIUS_METERS, probe)

    # Giữ thứ tự: 'place' (theo thứ tự thẻ) trước, rồi 'area' theo khoảng cách
    # tăng dần — hai danh sách nguồn đã có sẵn thứ tự đó. Trùng tên file thì giữ
    # lần xuất hiện ĐẦU, tức giữ nhãn 'place' thay vì hạ xuống 'area'.
    ordered: list[Candidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.title in seen:
            continue
        seen.add(candidate.title)
        ordered.append(candidate)

    details = file_details([candidate.title for candidate in ordered], probe) if ordered else {}

    rows: list[dict[str, Any]] = []
    for candidate in ordered:
        info = details.get(candidate.title)
        if not info:
            continue
        # README coi `license`/`attribution` là trường bắt buộc của hợp đồng ảnh:
        # "thiếu giấy phép hoặc tác giả thì ảnh không được hiển thị". Chặn TẠI
        # ĐÂY, chỗ dữ liệu vào hệ thống, thay vì để giao diện hạ xuống "Giấy
        # phép: chưa rõ" — dòng đó không phải ghi công, nó là lời thú nhận rằng
        # ta đang vi phạm điều kiện giấy phép.
        #
        # extmetadata của file nhập hàng loạt (Flickr cũ) hay thiếu hẳn Artist.
        # Nhưng CC0 và ảnh thuộc phạm vi công cộng KHÔNG đòi ghi tác giả, nên loại
        # chúng chỉ vì Artist trống là mất ảnh vô cớ — chỉ đòi `attribution` khi
        # chính giấy phép đòi.
        if not info["license"]:
            continue
        if not info["attribution"] and _requires_attribution(info["license"]):
            continue
        rows.append(
            {
                "source": "wikimedia",
                "source_ref": candidate.title,
                "url": info["url"],
                "thumb_url": info["thumbUrl"],
                "width": info["width"],
                "height": info["height"],
                "title": candidate.title,
                "confidence": candidate.confidence,
                # Khoảng cách chỉ có nghĩa với ảnh khu vực. Ảnh của chính địa
                # điểm mà gắn "cách 0 m" là bịa ra một phép đo không tồn tại.
                "distance_meters": candidate.dist if candidate.confidence == "area" else None,
                "license": info["license"],
                "attribution": info["attribution"],
                "source_url": info["sourceUrl"],
            }
        )
        if len(rows) >= MAX_PHOTOS:
            break

    if not rows and (not probe.get("ok") or probe.get("failed")):
        # Không lấy được ảnh nào VÀ có request hỏng ở đâu đó. Đây là "chưa
        # biết", KHÔNG phải "không có ảnh" — nên không ghi gì vào cache, để lần
        # sau còn dò lại. Ghi 'empty' ở đây là biến một lần rớt mạng thành kết
        # luận sai kéo dài `photo_cache_days` ngày.
        #
        # Phải kiểm cả `failed`, không được chỉ kiểm `not ok`: kịch bản hỏng
        # thật là geosearch THÀNH CÔNG (trả 20 ứng viên có thật) rồi imageinfo
        # mới rớt vì hết lượt thử 429. Lúc đó `ok` đã True, `rows` rỗng, và chỉ
        # nhìn `ok` thì hệ thống kết luận "quanh đây không có ảnh nào".
        return {"poiId": poi_id, "status": "unavailable", "fetchedAt": None, "photos": []}

    _store(poi_id, rows, "ready" if rows else "empty", database_url)
    # Đọc lại từ cache thay vì tự dựng payload: đảm bảo lần gọi đầu tiên và mọi
    # lần gọi sau trả về đúng cùng một thứ, kể cả `id` của từng tấm ảnh.
    return cached_photos(poi_id, limit, database_url) or {
        "poiId": poi_id,
        "status": "empty",
        "fetchedAt": None,
        "photos": [],
    }


def poi_photo_context(poi_id: str, database_url: str | None = None) -> dict[str, Any] | None:
    """Toạ độ + thẻ OSM gốc của một POI. ``None`` khi không có POI đó.

    Gom vào một truy vấn để endpoint ảnh không phải tự biết cấu trúc
    ``poi_source_records``. Lấy bản ghi nguồn MỚI NHẤT có thẻ: một POI có thể
    có nhiều bản ghi nguồn sau các lần đồng bộ.
    """
    with psycopg.connect(database_url or DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    ST_Y(p.location::geometry) AS latitude,
                    ST_X(p.location::geometry) AS longitude,
                    (
                        SELECT r.raw_payload->'tags'
                        FROM poi_source_records r
                        WHERE r.canonical_poi_id = p.id AND r.raw_payload ? 'tags'
                        ORDER BY r.last_seen_at DESC, r.id DESC
                        LIMIT 1
                    ) AS tags
                FROM pois p
                WHERE p.id = %s
                """,
                (poi_id,),
            )
            row = cursor.fetchone()
    if row is None:
        return None
    return {
        "latitude": float(row["latitude"]),
        "longitude": float(row["longitude"]),
        "tags": row["tags"] if isinstance(row["tags"], dict) else {},
    }
