-- Hồ sơ định tuyến XE MÁY cho TP.HCM (OSRM).
--
-- KẾ THỪA /opt/car.lua thay vì chép 511 dòng của nó. Chép ra một bản riêng thì
-- mỗi lần nâng cấp ảnh osrm/osrm-backend, bản chép sẽ lặng lẽ tụt lại phía sau
-- logic gốc (process_way, xử lý oneway, turn penalty...) mà không ai nhận ra.
-- Ở đây chỉ vá BẢNG CẤU HÌNH mà setup() trả về; toàn bộ hàm xử lý dùng lại y
-- nguyên của car.lua.
--
-- Vì sao xe máy KHÔNG phải là "ô tô chạy chậm hơn" — ba khác biệt thật:
--
--   1. Luật: xe máy BỊ CẤM trên đường cao tốc ở Việt Nam. Đây là khác biệt
--      quan trọng nhất và là thứ mà việc chỉ chỉnh tốc độ không mô tả được —
--      tuyến ô tô đi cao tốc vòng ngoài hoàn toàn không hợp lệ với xe máy.
--   2. Kích thước: hẻm TP.HCM thường gắn maxwidth/width nhỏ. car.lua lấy
--      vehicle_width = 1.9 m nên loại sạch những hẻm đó; xe máy rộng ~0.8 m đi
--      lọt, và hẻm chính là thứ làm tuyến xe máy ngắn hơn tuyến ô tô.
--   3. Thẻ OSM: `motorcycle=*` phải thắng `motor_vehicle=*`. Nhiều đường cấm
--      ô tô nhưng ghi rõ `motorcycle=yes`.
--
-- Dựng bằng scripts/build_osrm_motorbike.sh, chạy bởi service `osrm-motorbike`.

-- car.lua gọi require('lib/...') ở cấp cao nhất. OSRM chỉ thêm THƯ MỤC CHỨA
-- HỒ SƠ (/data) vào package.path, mà lib/ nằm ở /opt — không thêm dòng này
-- thì loadfile bên dưới chết ngay ở require đầu tiên.
package.path = '/opt/?.lua;' .. package.path

local car = assert(loadfile('/opt/car.lua'))()
local car_setup = car.setup

local function setup()
  local profile = car_setup()

  profile.properties.max_speed_for_map_matching = 120 / 3.6

  -- (1) Cấm cao tốc. Đặt cả `avoid` lẫn xoá khỏi bảng tốc độ: `avoid` là thứ
  -- thực sự loại bỏ way (lib/way_handlers.lua: `if profile.avoid[data.highway]`),
  -- còn xoá tốc độ để không ai đọc bảng này rồi tưởng xe máy vẫn đi được 90.
  profile.avoid['motorway'] = true
  profile.avoid['motorway_link'] = true
  profile.speeds.highway.motorway = nil
  profile.speeds.highway.motorway_link = nil

  -- (2) `motorcycle` phải đứng TRƯỚC motorcar/motor_vehicle trong thứ tự ưu
  -- tiên, vì lib/access.lua duyệt theo thứ tự và lấy thẻ khớp ĐẦU TIÊN.
  profile.access_tag_whitelist['motorcycle'] = true
  table.insert(profile.access_tags_hierarchy, 1, 'motorcycle')
  table.insert(profile.restrictions, 1, 'motorcycle')

  -- (3) Kích thước thật của xe máy. car.lua dùng các số này qua lib/measure.lua
  -- để loại way theo maxheight/maxwidth/width/maxlength/maxweight.
  profile.vehicle_height = 1.6
  profile.vehicle_width  = 0.8
  profile.vehicle_length = 2.0
  profile.vehicle_weight = 250

  -- (4) Tốc độ thực tế trong nội đô TP.HCM. Xe máy CHẬM HƠN ô tô trên đường
  -- lớn thông thoáng (giới hạn 40-60 km/h trong đô thị, và thực tế ít khi đạt)
  -- nhưng NHANH HƠN ở đường nhỏ vì không phải chờ nút thắt như ô tô.
  profile.speeds.highway.trunk          = 50
  profile.speeds.highway.trunk_link     = 30
  profile.speeds.highway.primary        = 40
  profile.speeds.highway.primary_link   = 25
  profile.speeds.highway.secondary      = 35
  profile.speeds.highway.secondary_link = 22
  profile.speeds.highway.tertiary       = 30
  profile.speeds.highway.tertiary_link  = 18
  profile.speeds.highway.unclassified   = 22
  profile.speeds.highway.residential    = 20
  profile.speeds.highway.living_street  = 10
  profile.speeds.highway.service        = 15

  -- (5) Hẻm không phải đường cùng bất đắc dĩ với xe máy mà là tuyến bình
  -- thường. car.lua phạt 0.5 (nửa tốc độ) để ô tô tránh chui vào hẻm; giữ
  -- nguyên mức phạt đó thì tuyến xe máy bị đẩy ra đường lớn y hệt ô tô và cả
  -- hồ sơ này thành vô nghĩa.
  profile.service_penalties.alley = 0.9

  return profile
end

return {
  setup        = setup,
  process_way  = car.process_way,
  process_node = car.process_node,
  process_turn = car.process_turn,
}
