"""Kiểm thử ảnh Wikimedia Commons cho trang chi tiết địa điểm.

KHÔNG bài nào chạm mạng thật: ``urlopen`` (hoặc chính ``commons_api``) bị thay
bằng hàm giả, và nhịp gọi bị đặt về 0 để bộ test không ngồi chờ ``_throttle()``.

Bốn rủi ro được canh riêng vì cả bốn đều im lặng:

- Thẻ ``image`` của OSM do người nhập gõ tay và trỏ được tới BẤT KỲ máy chủ nào.
  Nhúng thẳng vào trang là giao quyền quyết định nội dung cho bên thứ ba; hỏng
  kiểu này không báo lỗi, chỉ hiện ra một tấm ảnh.
- Trộn thứ tự ``place``/``area`` làm ảnh con phố trồi lên đầu và trông y hệt
  ảnh của quán — đúng loại nói dối mà nhãn ``confidence`` sinh ra để chặn.
- ``extmetadata.Artist`` là HTML. Lọt nguyên thẻ ``<a>`` ra giao diện thì phần
  ghi công đọc thành mã nguồn, và là một lỗ XSS nếu React dựng bằng
  ``dangerouslySetInnerHTML``.
- HTTP 429 ném ngoại lệ ra ngoài sẽ làm sập cả trang chi tiết chỉ vì một cái
  ảnh không tải được.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error

import pytest

from app import photos


@pytest.fixture(autouse=True)
def khong_gian_nhip(monkeypatch) -> None:
    """Bỏ hẳn nhịp chờ Wikimedia trong lúc test.

    ``_throttle`` ngủ tối thiểu một giây giữa hai request — đúng và cần thiết
    khi chạy thật, nhưng ở đây nó chỉ biến một bài test thử-lại-ba-lần thành ba
    giây chờ. Đặt hằng số về 0 thay vì vá ``time.sleep`` toàn cục.
    """
    monkeypatch.setattr(photos, "MIN_SECONDS_BETWEEN_CALLS", 0.0)
    monkeypatch.setattr(photos, "RETRY_BASE_DELAY_SECONDS", 0.0)


class FakeResponse:
    """Đủ giao diện cho ``json.load(response)`` và ``with ... as``."""

    def __init__(self, payload) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://commons.wikimedia.org/w/api.php", code, "loi gia lap", None, None
    )


def imageinfo_page(
    title: str,
    *,
    artist: str | None = "Nguyễn Văn A",
    license_name: str | None = "CC BY-SA 4.0",
) -> dict:
    """Một phần tử ``query.pages`` đúng hình dạng formatversion=2 trả về.

    Mặc định CÓ tác giả vì giấy phép mặc định ở đây là CC BY-SA — thứ bắt buộc
    ghi công, nên ``fetch_and_store`` loại thẳng ảnh thiếu tác giả. Để mặc định
    là ``None`` thì mọi bài test về THỨ TỰ ứng viên sẽ hỏng vì một lý do chẳng
    liên quan gì tới thứ tự. Bài nào muốn thử đúng ca thiếu ghi công thì truyền
    ``artist=None`` ra mặt.
    """
    slug = title.split(":", 1)[-1].replace(" ", "_")
    extmetadata: dict = {}
    if artist is not None:
        extmetadata["Artist"] = {"value": artist}
    if license_name is not None:
        extmetadata["LicenseShortName"] = {"value": license_name}
    return {
        "title": title,
        "imageinfo": [
            {
                "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{slug}",
                "thumburl": f"https://thumb.wikimedia.org/800px-{slug}",
                "thumbwidth": 800,
                "thumbheight": 600,
                "width": 4000,
                "height": 3000,
                "descriptionurl": f"https://commons.wikimedia.org/wiki/{slug}",
                "extmetadata": extmetadata,
            }
        ],
    }


# --- Lọc theo tên file --------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "File:Logo of Highlands Coffee.jpg",
        "File:Logos collection 2019.png",
        "File:Map of District 1 Ho Chi Minh City.png",
        "File:Coat of arms of Ho Chi Minh City.jpg",
        "File:Seal of the city.png",
        "File:Screenshot of the booking page.png",
        "File:QR code for menu.png",
        "File:Bus route diagram.png",
    ],
)
def test_loai_duoc_logo_ban_do_huy_hieu(title: str) -> None:
    """Geosearch trả về đủ thứ quanh một toạ độ: logo thương hiệu, bản đồ hành
    chính, ảnh chụp màn hình. Không lọc thì "ảnh khu vực" của một quán cà phê
    có thể là huy hiệu thành phố."""
    assert photos.is_plausible_place_photo(title) is False


@pytest.mark.parametrize(
    "title",
    ["File:Saigon.svg", "File:Menu.pdf", "File:Street.ogv", "File:Clip.webm", "File:Scan.tif"],
)
def test_loai_duoc_dinh_dang_the_img_khong_hien_duoc(title: str) -> None:
    """Danh sách TRẮNG đuôi file: .svg/.pdf/.ogv lọt vào thẻ <img> thì ô ảnh
    hiện ra một khung vỡ, không có lỗi nào ở console server."""
    assert photos.is_displayable_image(title) is False
    assert photos.is_plausible_place_photo(title) is False


@pytest.mark.parametrize(
    "title",
    [
        "File:Ben Thanh Market at night.jpg",
        "File:Nguyen Hue Boulevard 2019.jpeg",
        "File:Grand Hotel Saigon facade.JPG",
        "File:Quán cà phê trên đường Pasteur.png",
    ],
)
def test_giu_lai_anh_thuong(title: str) -> None:
    assert photos.is_plausible_place_photo(title) is True


@pytest.mark.parametrize(
    "title",
    ["File:Mapo tofu at a Saigon restaurant.jpg", "File:Flagship store on Dong Khoi.jpg"],
)
def test_khong_loai_oan_tu_chi_chua_chuoi_con(title: str) -> None:
    """So khớp theo TỪ chứ không theo chuỗi con. Theo chuỗi con thì "Mapo" dính
    "map" và "Flagship" dính "flag" — mất ảnh thật mà không ai biết."""
    assert photos.is_plausible_place_photo(title) is True


def test_anh_do_chinh_the_osm_chi_dinh_khong_bi_loc_theo_ten(monkeypatch) -> None:
    """Bất đối xứng CÓ CHỦ Ý: bộ lọc tên file chỉ áp cho ứng viên 'area'.

    Nếu chính thẻ OSM của địa điểm trỏ tới tấm ảnh đó thì đấy là ảnh chủ dữ liệu
    tự khai — ta không có tư cách đoán lại bằng regex tên file.
    """
    assert photos.is_plausible_place_photo("File:Logo of Highlands Coffee.jpg") is False

    monkeypatch.setattr(photos, "commons_api", lambda *a, **k: {})
    ung_vien = photos.photos_from_tags({"wikimedia_commons": "File:Logo of Highlands Coffee.jpg"})

    assert [(c.title, c.confidence) for c in ung_vien] == [
        ("File:Logo of Highlands Coffee.jpg", "place")
    ]


# --- photos_from_tags: nguồn 'place' ------------------------------------------


def test_nhan_the_wikimedia_commons_dang_file(monkeypatch) -> None:
    monkeypatch.setattr(photos, "commons_api", lambda *a, **k: {})
    ung_vien = photos.photos_from_tags({"wikimedia_commons": "File:Grand Hotel Saigon.jpg"})

    assert len(ung_vien) == 1
    assert ung_vien[0].title == "File:Grand Hotel Saigon.jpg"
    assert ung_vien[0].confidence == "place"
    # Ảnh của chính địa điểm KHÔNG có khoảng cách. "cách 0 m" là một phép đo
    # không hề tồn tại.
    assert ung_vien[0].dist is None


@pytest.mark.parametrize(
    "gia_tri",
    [
        "Grand_Hotel_Saigon.jpg",
        "File:Grand Hotel Saigon.jpg",
        "Image:Grand Hotel Saigon.jpg",
        "Tập tin:Grand Hotel Saigon.jpg",
        "  File:Grand_Hotel_Saigon.jpg  ",
    ],
)
def test_mo_cac_kieu_viet_the_ve_cung_mot_ten_file(monkeypatch, gia_tri: str) -> None:
    """Người nhập ở TP.HCM dùng cả 'File:', 'Image:' lẫn 'Tập tin:'. Không quy
    về một dạng thì cùng một tấm ảnh bị coi là nhiều ảnh khác nhau."""
    monkeypatch.setattr(photos, "commons_api", lambda *a, **k: {})
    ung_vien = photos.photos_from_tags({"wikimedia_commons": gia_tri})
    assert [c.title for c in ung_vien] == ["File:Grand Hotel Saigon.jpg"]


def test_category_duoc_mo_ra_thanh_tung_file(monkeypatch) -> None:
    da_goi: list[dict] = []

    def fake_commons_api(params, endpoint=photos.COMMONS_API_URL, probe=None):
        da_goi.append(params)
        return {
            "query": {
                "categorymembers": [
                    {"title": "File:Grand Hotel Saigon 01.jpg"},
                    {"title": "File:Grand Hotel Saigon 02.jpg"},
                ]
            }
        }

    monkeypatch.setattr(photos, "commons_api", fake_commons_api)
    ung_vien = photos.photos_from_tags({"wikimedia_commons": "Category:Grand Hotel, Saigon"})

    assert da_goi[0]["list"] == "categorymembers"
    assert da_goi[0]["cmtitle"] == "Category:Grand Hotel, Saigon"
    # gsnamespace tương đương ở đây: cmtype=file, nếu không thì lọt cả trang mô tả.
    assert da_goi[0]["cmtype"] == "file"
    assert [c.title for c in ung_vien] == [
        "File:Grand Hotel Saigon 01.jpg",
        "File:Grand Hotel Saigon 02.jpg",
    ]
    assert all(c.confidence == "place" for c in ung_vien)


def test_lay_anh_chinh_p18_tu_wikidata(monkeypatch) -> None:
    def fake_commons_api(params, endpoint=photos.COMMONS_API_URL, probe=None):
        assert endpoint == photos.WIKIDATA_API_URL
        assert params["property"] == "P18"
        return {
            "claims": {
                "P18": [{"mainsnak": {"datavalue": {"value": "Grand Hotel Saigon.jpg"}}}]
            }
        }

    monkeypatch.setattr(photos, "commons_api", fake_commons_api)
    ung_vien = photos.photos_from_tags({"wikidata": "Q10762720"})

    assert [c.title for c in ung_vien] == ["File:Grand Hotel Saigon.jpg"]


def test_qid_sai_dinh_dang_thi_khong_goi_wikidata(monkeypatch) -> None:
    def khong_duoc_goi(*args, **kwargs):  # pragma: no cover
        raise AssertionError("gọi Wikidata với một QID không hợp lệ")

    monkeypatch.setattr(photos, "commons_api", khong_duoc_goi)
    assert photos.photos_from_tags({"wikidata": "khong-phai-qid"}) == []


# --- photos_from_tags: chốt chặn bảo mật của thẻ `image` ----------------------


@pytest.mark.parametrize(
    "url_la",
    [
        "https://evil.example.com/anh-mien-phi.jpg",
        "https://cdn.quan-an.vn/uploads/mon-an.jpg",
        "https://upload.wikimedia.org.evil.com/wikipedia/commons/a/ab/Foo.jpg",
        "http://upload.wikimedia.org/wikipedia/commons/a/ab/Foo.jpg",
        "ftp://upload.wikimedia.org/Foo.jpg",
        "https://192.168.1.10/Foo.jpg",
    ],
)
def test_tu_choi_the_image_tro_toi_host_la(monkeypatch, url_la: str) -> None:
    """CHỐT CHẶN BẢO MẬT — có bài riêng vì hỏng ở đây không báo lỗi gì cả.

    Thẻ ``image`` của OSM do người dùng gõ tay. Nhận bừa thì trang chi tiết
    nhúng một URL bất kỳ: nội dung hiển thị do bên thứ ba quyết định, referrer
    của người dùng rò sang đó, và chẳng có giấy phép nào để ghi công.
    ``http://`` cũng bị từ chối — nội dung không mã hoá trong trang HTTPS.
    """
    def khong_duoc_goi(*args, **kwargs):  # pragma: no cover
        raise AssertionError("gọi mạng cho một URL ảnh ngoài Wikimedia")

    monkeypatch.setattr(photos, "commons_api", khong_duoc_goi)
    assert photos.photos_from_tags({"image": url_la}) == []


@pytest.mark.parametrize(
    "url,ten_mong_doi",
    [
        (
            "https://upload.wikimedia.org/wikipedia/commons/a/ab/Ben_Thanh_Market.jpg",
            "File:Ben Thanh Market.jpg",
        ),
        (
            "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/"
            "Ben_Thanh_Market.jpg/800px-Ben_Thanh_Market.jpg",
            "File:Ben Thanh Market.jpg",
        ),
        (
            "https://commons.wikimedia.org/wiki/File:Ben_Thanh_Market.jpg",
            "File:Ben Thanh Market.jpg",
        ),
        (
            "https://upload.wikimedia.org/wikipedia/commons/1/12/B%E1%BA%BFn_Th%C3%A0nh.jpg",
            "File:Bến Thành.jpg",
        ),
    ],
)
def test_nhan_the_image_tro_toi_wikimedia_va_quy_ve_ten_file(
    monkeypatch, url: str, ten_mong_doi: str
) -> None:
    """Luôn quy về TÊN FILE chứ không dùng thẳng URL: chỉ có tên file mới hỏi
    được giấy phép và tác giả, mà ảnh Commons không được hiện thiếu ghi công.
    Bản thu nhỏ '800px-Foo.jpg' phải quy về 'Foo.jpg', nếu không thì Commons
    báo không tìm thấy và ảnh biến mất."""
    monkeypatch.setattr(photos, "commons_api", lambda *a, **k: {})
    assert [c.title for c in photos.photos_from_tags({"image": url})] == [ten_mong_doi]


@pytest.mark.parametrize("the", [None, {}, {"image": "   "}, {"wikimedia_commons": ""}, []])
def test_the_rong_thi_tra_danh_sach_rong(monkeypatch, the) -> None:
    def khong_duoc_goi(*args, **kwargs):  # pragma: no cover
        raise AssertionError("gọi mạng cho một POI không có thẻ ảnh nào")

    monkeypatch.setattr(photos, "commons_api", khong_duoc_goi)
    assert photos.photos_from_tags(the) == []


# --- Thứ tự ứng viên ----------------------------------------------------------


def test_area_xep_gan_truoc_xa(monkeypatch) -> None:
    def fake_commons_api(params, endpoint=photos.COMMONS_API_URL, probe=None):
        return {
            "query": {
                "geosearch": [
                    {"title": "File:Xa.jpg", "dist": 280.4},
                    {"title": "File:Gan.jpg", "dist": 22.0},
                    {"title": "File:Giua.jpg", "dist": 131.7},
                ]
            }
        }

    monkeypatch.setattr(photos, "commons_api", fake_commons_api)
    ket_qua = photos.photos_near(10.7757, 106.7009)

    assert [c.title for c in ket_qua] == ["File:Gan.jpg", "File:Giua.jpg", "File:Xa.jpg"]
    assert [c.dist for c in ket_qua] == [22.0, 131.7, 280.4]
    assert all(c.confidence == "area" for c in ket_qua)


def test_area_thieu_khoang_cach_thi_xuong_cuoi(monkeypatch) -> None:
    """Nhãn giao diện là "Ảnh khu vực · cách N m". Không có N thì tấm ảnh đó
    kém tin nhất, nên nó phải xếp sau chứ không trồi lên đầu."""
    monkeypatch.setattr(
        photos,
        "commons_api",
        lambda *a, **k: {
            "query": {
                "geosearch": [
                    {"title": "File:Khong ro.jpg"},
                    {"title": "File:Gan.jpg", "dist": 40.0},
                ]
            }
        },
    )
    ket_qua = photos.photos_near(10.7757, 106.7009)

    assert [c.title for c in ket_qua] == ["File:Gan.jpg", "File:Khong ro.jpg"]
    assert ket_qua[-1].dist is None


def test_geosearch_chi_hoi_khong_gian_ten_file(monkeypatch) -> None:
    """Thiếu gsnamespace=6 thì geosearch trả về cả tên BÀI VIẾT, mà tên bài viết
    không phải tên file — hỏi imageinfo cho nó thì rỗng, và ô ảnh trống không rõ
    vì sao."""
    da_goi: dict = {}

    def fake_commons_api(params, endpoint=photos.COMMONS_API_URL, probe=None):
        da_goi.update(params)
        return {}

    monkeypatch.setattr(photos, "commons_api", fake_commons_api)
    photos.photos_near(10.7757, 106.7009, radius_m=300)

    assert da_goi["gsnamespace"] == 6
    assert da_goi["gscoord"] == "10.775700|106.700900"
    assert da_goi["gsradius"] == 300


@pytest.mark.parametrize("ban_kinh,mong_doi", [(1, 10), (99_999, 10_000), (300, 300)])
def test_ban_kinh_bi_kep_ve_khoang_api_chap_nhan(monkeypatch, ban_kinh, mong_doi) -> None:
    """API chỉ nhận 10-10.000 m và từ chối CẢ LỆNH nếu ngoài khoảng."""
    da_goi: dict = {}

    def fake_commons_api(params, endpoint=photos.COMMONS_API_URL, probe=None):
        da_goi.update(params)
        return {}

    monkeypatch.setattr(photos, "commons_api", fake_commons_api)
    photos.photos_near(10.7757, 106.7009, radius_m=ban_kinh)

    assert da_goi["gsradius"] == mong_doi


def _fake_api_place_va_area(monkeypatch, place_titles, area_items):
    """Ghép sẵn một Commons giả: thẻ trỏ tới category, quanh đó có ảnh khu vực."""

    def fake_commons_api(params, endpoint=photos.COMMONS_API_URL, probe=None):
        if probe is not None:
            probe["ok"] = True
        if params.get("list") == "categorymembers":
            return {"query": {"categorymembers": [{"title": t} for t in place_titles]}}
        if params.get("list") == "geosearch":
            return {"query": {"geosearch": list(area_items)}}
        if params.get("prop") == "imageinfo":
            titles = params["titles"].split("|")
            return {"query": {"pages": [imageinfo_page(t) for t in titles]}}
        return {}

    monkeypatch.setattr(photos, "commons_api", fake_commons_api)


def test_place_luon_dung_truoc_area(monkeypatch) -> None:
    """Ảnh con phố trồi lên trên ảnh của chính quán thì người xem đọc tấm đầu
    tiên như "ảnh đại diện" — nhãn nhỏ bên dưới không cứu được ấn tượng đó."""
    _fake_api_place_va_area(
        monkeypatch,
        ["File:Grand Hotel Saigon 01.jpg"],
        [
            {"title": "File:Dong Khoi xa.jpg", "dist": 240.0},
            {"title": "File:Dong Khoi gan.jpg", "dist": 18.5},
        ],
    )
    da_ghi: dict = {}
    monkeypatch.setattr(
        photos, "_store", lambda poi_id, rows, status, url=None: da_ghi.update(rows=rows, status=status)
    )
    monkeypatch.setattr(photos, "cached_photos", lambda *a, **k: None)

    photos.fetch_and_store(
        "poi-1", 10.7757, 106.7009, {"wikimedia_commons": "Category:Grand Hotel, Saigon"}
    )

    rows = da_ghi["rows"]
    assert [r["confidence"] for r in rows] == ["place", "area", "area"]
    assert [r["title"] for r in rows] == [
        "File:Grand Hotel Saigon 01.jpg",
        "File:Dong Khoi gan.jpg",
        "File:Dong Khoi xa.jpg",
    ]
    # Khoảng cách chỉ có nghĩa với ảnh khu vực.
    assert rows[0]["distance_meters"] is None
    assert [r["distance_meters"] for r in rows[1:]] == [18.5, 240.0]
    assert da_ghi["status"] == "ready"


def test_trung_ten_file_thi_giu_nhan_place(monkeypatch) -> None:
    """Cùng một tấm ảnh vừa được thẻ OSM chỉ tới vừa nằm trong geosearch. Giữ
    lần xuất hiện sau là HẠ CẤP nó xuống "ảnh khu vực" — nói giảm đi so với sự
    thật cũng là nói sai."""
    _fake_api_place_va_area(
        monkeypatch,
        ["File:Grand Hotel Saigon 01.jpg"],
        [{"title": "File:Grand Hotel Saigon 01.jpg", "dist": 5.0}],
    )
    da_ghi: dict = {}
    monkeypatch.setattr(
        photos, "_store", lambda poi_id, rows, status, url=None: da_ghi.update(rows=rows)
    )
    monkeypatch.setattr(photos, "cached_photos", lambda *a, **k: None)

    photos.fetch_and_store(
        "poi-1", 10.7757, 106.7009, {"wikimedia_commons": "Category:Grand Hotel, Saigon"}
    )

    assert len(da_ghi["rows"]) == 1
    assert da_ghi["rows"][0]["confidence"] == "place"
    assert da_ghi["rows"][0]["distance_meters"] is None


def test_du_anh_cua_chinh_dia_diem_thi_khong_di_tim_anh_khu_vuc(monkeypatch) -> None:
    """Có ảnh thật rồi mà độn thêm ảnh con phố thì chỉ làm loãng."""
    du_anh = [f"File:Grand Hotel Saigon {i:02d}.jpg" for i in range(1, photos.MAX_PHOTOS + 1)]

    def fake_commons_api(params, endpoint=photos.COMMONS_API_URL, probe=None):
        if probe is not None:
            probe["ok"] = True
        if params.get("list") == "geosearch":  # pragma: no cover
            raise AssertionError("đã đủ ảnh 'place' mà vẫn gọi geosearch")
        if params.get("list") == "categorymembers":
            return {"query": {"categorymembers": [{"title": t} for t in du_anh]}}
        if params.get("prop") == "imageinfo":
            return {
                "query": {"pages": [imageinfo_page(t) for t in params["titles"].split("|")]}
            }
        return {}

    monkeypatch.setattr(photos, "commons_api", fake_commons_api)
    da_ghi: dict = {}
    monkeypatch.setattr(
        photos, "_store", lambda poi_id, rows, status, url=None: da_ghi.update(rows=rows)
    )
    monkeypatch.setattr(photos, "cached_photos", lambda *a, **k: None)

    photos.fetch_and_store(
        "poi-1", 10.7757, 106.7009, {"wikimedia_commons": "Category:Grand Hotel, Saigon"}
    )

    assert len(da_ghi["rows"]) == photos.MAX_PHOTOS
    assert all(r["confidence"] == "place" for r in da_ghi["rows"])


# --- Giấy phép và ghi công ----------------------------------------------------


@pytest.mark.parametrize(
    "artist_html,mong_doi",
    [
        (
            '<a href="//commons.wikimedia.org/wiki/User:Mcoghlan" title="User:Mcoghlan">'
            "Michael Coghlan</a> from Adelaide, Australia",
            "Michael Coghlan from Adelaide, Australia",
        ),
        ('<span class="fn">Nguyễn Văn A</span>', "Nguyễn Văn A"),
        ("Trần Thị B &amp; cộng sự", "Trần Thị B & cộng sự"),
        ('<a href="#">Tên</a>&nbsp;có&nbsp;entity', "Tên có entity"),
    ],
)
def test_boc_the_html_trong_ten_tac_gia(monkeypatch, artist_html: str, mong_doi: str) -> None:
    """``extmetadata.Artist`` trả về HTML, gần như luôn là một thẻ ``<a>``. Bóc
    tại chỗ dữ liệu VÀO hệ thống, chứ không trông chờ mọi nơi hiển thị đều nhớ
    bóc — và nếu phía React có chỗ nào dùng ``dangerouslySetInnerHTML`` thì đây
    là chênh lệch giữa một dòng ghi công và một lỗ XSS."""
    monkeypatch.setattr(
        photos,
        "commons_api",
        lambda *a, **k: {
            "query": {"pages": [imageinfo_page("File:X.jpg", artist=artist_html)]}
        },
    )
    chi_tiet = photos.file_details(["File:X.jpg"])

    assert chi_tiet["File:X.jpg"]["attribution"] == mong_doi


@pytest.mark.parametrize(
    "gia_tri,mong_doi",
    [
        ("&lt;img src=x onerror=alert(1)&gt;", None),
        ("&lt;svg onload=alert(1)&gt;", None),
        (
            '<a href="x">&lt;script&gt;alert(document.cookie)&lt;/script&gt;</a>',
            "alert(document.cookie)",
        ),
        ("&#60;script&#62;alert(1)&#60;/script&#62;", "alert(1)"),
    ],
)
def test_html_da_ma_hoa_entity_khong_duoc_dung_lai_thanh_the(gia_tri: str, mong_doi) -> None:
    """Bài test này canh THỨ TỰ của hai phép biến đổi trong ``_plain_text``.

    Bóc thẻ trước rồi mới giải mã entity thì regex không nhìn thấy phần đã mã
    hoá ở bước một, và bước hai dựng nó thành thẻ SỐNG: hàm được quảng cáo là
    hàng rào chống XSS lại đi SINH RA HTML từ một chuỗi vốn vô hại. Bộ test cũ
    chỉ thử HTML thường nên để lọt đúng cái ca này.
    """
    ket_qua = photos._plain_text(gia_tri)

    assert ket_qua == mong_doi
    assert ket_qua is None or "<" not in ket_qua


def test_khong_co_tac_gia_thi_de_none_chu_khong_de_chuoi_rong(monkeypatch) -> None:
    """Chuỗi rỗng hiện ra giao diện thành dòng ghi công trống; ``None`` để giao
    diện biết mà ghi "không rõ tác giả"."""
    monkeypatch.setattr(
        photos,
        "commons_api",
        lambda *a, **k: {
            "query": {"pages": [imageinfo_page("File:X.jpg", artist="", license_name=None)]}
        },
    )
    chi_tiet = photos.file_details(["File:X.jpg"])

    assert chi_tiet["File:X.jpg"]["attribution"] is None
    assert chi_tiet["File:X.jpg"]["license"] is None


def _fake_api_mot_anh_khu_vuc(monkeypatch, *, artist, license_name) -> dict:
    """Commons giả: quanh toạ độ có đúng MỘT ảnh, giấy phép và tác giả cho sẵn.

    Trả về dict mà ``_store`` giả sẽ ghi vào, để bài test đọc ``rows``/``status``.
    """

    def fake_commons_api(params, endpoint=photos.COMMONS_API_URL, probe=None):
        if probe is not None:
            probe["ok"] = True
        if params.get("list") == "geosearch":
            return {"query": {"geosearch": [{"title": "File:Dong Khoi gan.jpg", "dist": 18.5}]}}
        if params.get("prop") == "imageinfo":
            return {
                "query": {
                    "pages": [
                        imageinfo_page(
                            "File:Dong Khoi gan.jpg", artist=artist, license_name=license_name
                        )
                    ]
                }
            }
        return {}

    da_ghi: dict = {}
    monkeypatch.setattr(photos, "commons_api", fake_commons_api)
    monkeypatch.setattr(
        photos,
        "_store",
        lambda poi_id, rows, status, url=None: da_ghi.update(rows=rows, status=status),
    )
    monkeypatch.setattr(photos, "cached_photos", lambda *a, **k: None)
    return da_ghi


@pytest.mark.parametrize(
    "artist,license_name",
    [
        (None, "CC BY-SA 4.0"),
        (None, "CC BY 2.0"),
        ("Nguyễn Văn A", None),
    ],
)
def test_bo_anh_khi_thieu_giay_phep_hoac_thieu_tac_gia_ma_giay_phep_doi(
    monkeypatch, artist, license_name
) -> None:
    """README hứa: thiếu giấy phép hoặc tác giả thì ảnh KHÔNG được hiển thị.

    Giao diện hạ xuống "Giấy phép: chưa rõ" không phải là ghi công — đó là lời
    thú nhận rằng ta đang trưng một tấm ảnh CC BY mà không ghi tên người chụp.
    Chặn tại chỗ dữ liệu VÀO hệ thống thì cache, API và giao diện tự đúng theo.

    ``status`` vẫn là ``empty`` chứ không phải ``unavailable``: đã hỏi được
    Wikimedia rồi, chỉ là quanh đây không có ảnh DÙNG ĐƯỢC.
    """
    da_ghi = _fake_api_mot_anh_khu_vuc(monkeypatch, artist=artist, license_name=license_name)

    photos.fetch_and_store("poi-1", 10.7757, 106.7009, {})

    assert da_ghi == {"rows": [], "status": "empty"}


@pytest.mark.parametrize("giay_phep", ["CC0", "CC0 1.0", "Public domain", "PD-US-expired"])
def test_giu_anh_cc0_va_pham_vi_cong_cong_du_thieu_tac_gia(monkeypatch, giay_phep: str) -> None:
    """Không phải giấy phép nào cũng đòi ghi tên tác giả.

    CC0 và ảnh đã vào phạm vi công cộng thì không đòi, nên loại chúng chỉ vì
    ``extmetadata`` trống Artist là mất ảnh vô cớ — một dạng sai số liệu theo
    hướng ngược lại.
    """
    da_ghi = _fake_api_mot_anh_khu_vuc(monkeypatch, artist=None, license_name=giay_phep)

    photos.fetch_and_store("poi-1", 10.7757, 106.7009, {})

    assert [r["title"] for r in da_ghi["rows"]] == ["File:Dong Khoi gan.jpg"]
    assert da_ghi["rows"][0]["license"] == giay_phep
    # "Chưa biết tác giả" vẫn là None chứ không bị bịa thành chuỗi rỗng.
    assert da_ghi["rows"][0]["attribution"] is None
    assert da_ghi["status"] == "ready"


def test_bo_qua_file_khong_ton_tai_tren_commons(monkeypatch) -> None:
    """Thẻ OSM trỏ tới một file đã bị xoá khỏi Commons. Giữ lại thì hiện một URL
    chết."""
    monkeypatch.setattr(
        photos,
        "commons_api",
        lambda *a, **k: {
            "query": {
                "pages": [
                    {"title": "File:Da xoa.jpg", "missing": True},
                    imageinfo_page("File:Con song.jpg"),
                ]
            }
        },
    )
    chi_tiet = photos.file_details(["File:Da xoa.jpg", "File:Con song.jpg"])

    assert "File:Da xoa.jpg" not in chi_tiet
    assert "File:Con song.jpg" in chi_tiet


def test_gop_nhieu_file_vao_mot_request(monkeypatch) -> None:
    """Gọi từng file một thì tám tấm ảnh là tám giây chờ vì `_throttle()` —
    người dùng bỏ trang trước khi ảnh kịp về."""
    so_lan: list[dict] = []

    def fake_commons_api(params, endpoint=photos.COMMONS_API_URL, probe=None):
        so_lan.append(params)
        return {"query": {"pages": [imageinfo_page(t) for t in params["titles"].split("|")]}}

    monkeypatch.setattr(photos, "commons_api", fake_commons_api)
    titles = [f"File:Anh {i}.jpg" for i in range(8)]
    chi_tiet = photos.file_details(titles + [titles[0]])

    assert len(so_lan) == 1
    assert len(chi_tiet) == 8


# --- commons_api: 429, lỗi mạng, và cái probe -------------------------------


def test_gap_429_thi_thu_lai_roi_tra_rong_chu_khong_nem(monkeypatch) -> None:
    """Wikimedia CÓ giới hạn tần suất thật. Ngoại lệ lọt ra khỏi đây sẽ làm sập
    cả endpoint ảnh chỉ vì một lần gọi dồn."""
    so_lan = {"n": 0}

    def fake_urlopen(request, timeout=None):
        so_lan["n"] += 1
        raise http_error(429)

    monkeypatch.setattr(photos.urllib.request, "urlopen", fake_urlopen)

    ket_qua = photos.commons_api({"action": "query"})

    assert ket_qua == {}
    assert so_lan["n"] == photos.MAX_ATTEMPTS


def test_429_roi_thanh_cong_o_lan_thu_hai(monkeypatch) -> None:
    so_lan = {"n": 0}

    def fake_urlopen(request, timeout=None):
        so_lan["n"] += 1
        if so_lan["n"] == 1:
            raise http_error(429)
        return FakeResponse({"query": {"geosearch": []}})

    monkeypatch.setattr(photos.urllib.request, "urlopen", fake_urlopen)

    assert photos.commons_api({"action": "query"}) == {"query": {"geosearch": []}}
    assert so_lan["n"] == 2


@pytest.mark.parametrize("ma_loi", [400, 403, 404])
def test_loi_khong_phai_gioi_han_tan_suat_thi_khong_thu_lai(monkeypatch, ma_loi: int) -> None:
    """404 thử lại ba lần là ba giây chờ vô ích cho mỗi POI có thẻ ảnh hỏng."""
    so_lan = {"n": 0}

    def fake_urlopen(request, timeout=None):
        so_lan["n"] += 1
        raise http_error(ma_loi)

    monkeypatch.setattr(photos.urllib.request, "urlopen", fake_urlopen)

    assert photos.commons_api({"action": "query"}) == {}
    assert so_lan["n"] == 1


@pytest.mark.parametrize(
    "loi",
    [
        urllib.error.URLError("mat mang"),
        OSError("connection reset"),
        TimeoutError("qua han"),
    ],
)
def test_mat_mang_thi_tra_rong_chu_khong_nem(monkeypatch, loi) -> None:
    def fake_urlopen(request, timeout=None):
        raise loi

    monkeypatch.setattr(photos.urllib.request, "urlopen", fake_urlopen)
    assert photos.commons_api({"action": "query"}) == {}


def test_json_hong_thi_tra_rong_chu_khong_nem(monkeypatch) -> None:
    class RacKhongPhaiJson:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"<html>502 Bad Gateway</html>"

    monkeypatch.setattr(photos.urllib.request, "urlopen", lambda *a, **k: RacKhongPhaiJson())
    assert photos.commons_api({"action": "query"}) == {}


def test_probe_phan_biet_khong_co_gi_voi_khong_goi_duoc(monkeypatch) -> None:
    """Đây là thứ quyết định ``empty`` hay ``unavailable``, tức là chênh lệch
    giữa "đã hỏi Wikimedia, quanh đây không có ảnh" và "chưa hỏi được"."""
    monkeypatch.setattr(
        photos.urllib.request,
        "urlopen",
        lambda *a, **k: FakeResponse({"query": {"geosearch": []}}),
    )
    probe_tra_loi_rong: dict = {}
    photos.commons_api({"action": "query"}, probe=probe_tra_loi_rong)
    assert probe_tra_loi_rong["ok"] is True

    def fake_urlopen(request, timeout=None):
        raise http_error(429)

    monkeypatch.setattr(photos.urllib.request, "urlopen", fake_urlopen)
    probe_khong_goi_duoc: dict = {}
    photos.commons_api({"action": "query"}, probe=probe_khong_goi_duoc)
    assert probe_khong_goi_duoc["ok"] is False


def test_khong_goi_duoc_wikimedia_thi_tra_unavailable_va_khong_ghi_cache(monkeypatch) -> None:
    """Ghi 'empty' ở đây là biến một lần rớt mạng thành kết luận sai kéo dài
    `photo_cache_days` ngày."""
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("mat mang")

    monkeypatch.setattr(photos.urllib.request, "urlopen", fake_urlopen)

    def khong_duoc_ghi(*args, **kwargs):  # pragma: no cover
        raise AssertionError("ghi cache trong khi chưa gọi được Wikimedia")

    monkeypatch.setattr(photos, "_store", khong_duoc_ghi)

    ket_qua = photos.fetch_and_store("poi-1", 10.7757, 106.7009, {"image": "Foo.jpg"})

    assert ket_qua["status"] == "unavailable"
    assert ket_qua["photos"] == []
    assert ket_qua["fetchedAt"] is None


def test_hoi_duoc_nhung_quanh_day_khong_co_anh_thi_ghi_empty(monkeypatch) -> None:
    """``empty`` là một câu trả lời ĐÃ kiểm chứng, nên nó được phép vào cache."""
    monkeypatch.setattr(
        photos.urllib.request,
        "urlopen",
        lambda *a, **k: FakeResponse({"query": {"geosearch": []}}),
    )
    da_ghi: dict = {}
    monkeypatch.setattr(
        photos, "_store", lambda poi_id, rows, status, url=None: da_ghi.update(rows=rows, status=status)
    )
    monkeypatch.setattr(photos, "cached_photos", lambda *a, **k: None)

    ket_qua = photos.fetch_and_store("poi-1", 10.7757, 106.7009, {})

    assert da_ghi == {"rows": [], "status": "empty"}
    assert ket_qua["status"] == "empty"


def test_geosearch_xong_roi_imageinfo_rot_thi_khong_duoc_ghi_cache(monkeypatch) -> None:
    """Rớt mạng GIỮA CHỪNG không được biến thành kết luận "không có ảnh".

    Đây là lỗi đã từng có thật trong ``fetch_and_store``. Một lần dò gọi hai
    request nối tiếp, và cờ ``probe["ok"]`` chỉ bật lên chứ không bao giờ tắt.
    Khi geosearch THÀNH CÔNG (trả về ứng viên có thật) rồi imageinfo mới rớt vì
    hết lượt thử 429, ``rows`` rỗng nhưng ``ok`` vẫn là True — nên chốt chặn cũ
    ``if not rows and not probe.get("ok")`` không nổ, và hệ thống ghi
    ``status='empty'`` = "đã hỏi Wikimedia, quanh đây không có ảnh nào" vào
    cache suốt ``photo_cache_days`` ngày. Một lần mạng chập thành một lời
    khẳng định sai kéo dài 30 ngày.
    """

    def fake_commons_api(params, endpoint=photos.COMMONS_API_URL, probe=None):
        if params.get("list") == "geosearch":
            if probe is not None:
                probe["ok"] = True
            return {
                "query": {"geosearch": [{"title": "File:Dong Khoi gan.jpg", "dist": 18.5}]}
            }
        # imageinfo rớt: đúng cách commons_api đánh dấu khi hết lượt thử lại.
        if probe is not None:
            probe.setdefault("ok", False)
            probe["failed"] = True
        return {}

    monkeypatch.setattr(photos, "commons_api", fake_commons_api)

    def khong_duoc_ghi(*args, **kwargs):
        raise AssertionError("Không được ghi cache khi chưa hỏi xong Wikimedia")

    monkeypatch.setattr(photos, "_store", khong_duoc_ghi)

    ket_qua = photos.fetch_and_store("poi-1", 10.7757, 106.7009, {})

    assert ket_qua["status"] == "unavailable"
    assert ket_qua["photos"] == []
    assert ket_qua["fetchedAt"] is None


# --- Trần tài nguyên: ngân sách thời gian và số lượt dò đồng thời -------------
#
# Hai bài dưới đây cố tình KHÔNG dựa vào fixture `khong_gian_nhip`: thứ chúng
# canh chính là phần chờ mà fixture đó xoá đi. Endpoint ảnh là `def` nên FastAPI
# chạy nó trong threadpool 40 luồng dùng CHUNG với mọi endpoint khác; một lượt
# dò treo lâu không chỉ làm chậm ô ảnh, nó ăn mất slot của /health và /search.


def test_han_chot_tinh_ca_luc_xep_hang_truoc_khoa_giai_nhip(monkeypatch) -> None:
    """Hạn chót phải được kiểm NGAY TẠI chỗ chờ khoá, không chỉ quanh lần gọi mạng.

    Phần chờ dài nhất của một lượt dò nguội là chờ TRƯỚC ``_throttle_lock``:
    mười luồng xếp hàng thì luồng thứ mười chờ mười giây mà vẫn giữ nguyên một
    slot threadpool. Bài test dựng đúng cảnh đó bằng một luồng giữ khoá thật lâu
    hơn ngân sách, rồi đòi ``fetch_and_store`` bỏ cuộc đúng hạn.
    """
    monkeypatch.setattr(photos.settings, "photo_fetch_budget_seconds", 0.3)

    def khong_duoc_goi_mang(*args, **kwargs):  # pragma: no cover
        raise AssertionError("gọi mạng sau khi đã hết ngân sách")

    def khong_duoc_ghi(*args, **kwargs):  # pragma: no cover
        raise AssertionError("ghi cache trong khi chưa dò xong")

    monkeypatch.setattr(photos.urllib.request, "urlopen", khong_duoc_goi_mang)
    monkeypatch.setattr(photos, "_store", khong_duoc_ghi)

    nha_khoa = threading.Event()

    def giu_khoa() -> None:
        with photos._throttle_lock:
            nha_khoa.wait(5.0)

    nguoi_giu = threading.Thread(target=giu_khoa)
    nguoi_giu.start()
    try:
        # Chắc chắn khoá đã nằm trong tay luồng kia trước khi đo.
        time.sleep(0.05)
        bat_dau = time.monotonic()
        ket_qua = photos.fetch_and_store("poi-1", 10.7757, 106.7009, {})
        troi_qua = time.monotonic() - bat_dau
    finally:
        nha_khoa.set()
        nguoi_giu.join()

    # Hết giờ là "CHƯA dò được", không bao giờ là "không có ảnh" — và không ghi
    # cache, để lần sau còn dò lại.
    assert ket_qua["status"] == "unavailable"
    assert ket_qua["photos"] == []
    assert ket_qua["fetchedAt"] is None
    assert troi_qua < 1.5


def test_vuot_tran_luot_do_dong_thoi_thi_tra_unavailable_ngay(monkeypatch) -> None:
    """Vượt trần thì TRẢ NGAY chứ không xếp hàng.

    Xếp hàng vẫn là giữ slot threadpool — đúng thứ cần nhả ra. Rate limit ở
    api.py không thay được chốt này: khoá của nó là header X-Session-ID do chính
    client tự khai, đổi UUID mỗi request là có ngay một hạn mức mới.
    """
    monkeypatch.setattr(photos, "FETCH_SLOT_WAIT_SECONDS", 0.05)

    def khong_duoc_goi(*args, **kwargs):  # pragma: no cover
        raise AssertionError("đi dò Wikimedia dù đã hết slot")

    monkeypatch.setattr(photos, "commons_api", khong_duoc_goi)
    monkeypatch.setattr(photos, "_store", khong_duoc_goi)

    da_giu = 0
    try:
        for _ in range(photos.MAX_CONCURRENT_FETCHES):
            assert photos._fetch_slots.acquire(timeout=1.0)
            da_giu += 1
        ket_qua = photos.fetch_and_store("poi-1", 10.7757, 106.7009, {})
    finally:
        for _ in range(da_giu):
            photos._fetch_slots.release()

    assert ket_qua["status"] == "unavailable"
    assert ket_qua["photos"] == []
    assert ket_qua["fetchedAt"] is None


def test_xoa_han_chot_sau_moi_luot_do(monkeypatch) -> None:
    """Luồng threadpool được DÙNG LẠI cho request sau.

    Không xoá hạn chót ở ``finally`` thì request kế thừa hạn chót đã hết của
    request trước và bỏ cuộc ngay từ lần gọi đầu — hỏng im lặng, mọi POI đều
    thành 'unavailable' mà không có lỗi nào trong log.
    """
    monkeypatch.setattr(photos, "commons_api", lambda *a, **k: {})
    monkeypatch.setattr(photos, "_store", lambda *a, **k: None)
    monkeypatch.setattr(photos, "cached_photos", lambda *a, **k: None)

    photos.fetch_and_store("poi-1", 10.7757, 106.7009, {})

    assert getattr(photos._deadline, "at", None) is None


# --- User-Agent ---------------------------------------------------------------


def test_user_agent_mo_ta_du_an_va_khong_chua_thong_tin_ca_nhan(monkeypatch) -> None:
    """Wikimedia chặn User-Agent mặc định của thư viện, nên chuỗi này bắt buộc
    phải có. Nhưng nó là định danh của DỰ ÁN: mọi thông tin cá nhân nhét vào
    đây đi ra ngoài trong TỪNG request, vĩnh viễn, tới một bên thứ ba."""
    assert "NearbyPOI" in photos.USER_AGENT
    assert "@" not in photos.USER_AGENT

    da_gui: dict = {}

    def fake_urlopen(request, timeout=None):
        da_gui["ua"] = request.get_header("User-agent")
        return FakeResponse({})

    monkeypatch.setattr(photos.urllib.request, "urlopen", fake_urlopen)
    photos.commons_api({"action": "query"})

    assert da_gui["ua"] == photos.USER_AGENT
