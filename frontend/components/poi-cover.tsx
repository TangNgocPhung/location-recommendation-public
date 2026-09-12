'use client';

import {
  Banknote,
  Beer,
  BookOpen,
  Building2,
  Coffee,
  Croissant,
  CreditCard,
  Drama,
  Dumbbell,
  Film,
  Fuel,
  GraduationCap,
  Hotel,
  Landmark,
  Library,
  type LucideIcon,
  MapPin,
  Palette,
  Pill,
  School,
  Shirt,
  ShoppingBag,
  ShoppingCart,
  Smartphone,
  Store,
  Stethoscope,
  ToyBrick,
  Trees,
  UtensilsCrossed,
  Warehouse,
} from 'lucide-react';

import { cn } from '@/lib/utils';

type CoverStyle = { icon: LucideIcon; surface: string };

// Bảng tra CỨNG theo category, không băm chuỗi, không Math.random: cùng một
// địa điểm phải cho ra đúng một ảnh bìa ở mọi lần render — kể cả render trên
// máy chủ rồi hydrate lại ở trình duyệt, nếu không React sẽ báo lệch DOM.
//
// Mỗi ô có SẴN một màu nền đặc rồi mới chồng gradient lên. Gradient Tailwind
// phụ thuộc biến --tw-gradient-*; nếu vì lý do gì đó lớp đó không sinh ra thì
// thẻ vẫn là một mảng màu tử tế chứ không phải ô trắng trong buổi bảo vệ.
//
// 27 category dưới đây là TOÀN BỘ giá trị có thật trong bảng pois (đã đếm trên
// DB đang chạy), cộng vài bí danh mà giao diện hay gọi tên khác ('shopping').
const COVER_STYLES: Record<string, CoverStyle> = {
  cafe: {
    icon: Coffee,
    surface: 'bg-amber-600 bg-linear-to-br from-amber-500 via-orange-600 to-amber-800',
  },
  restaurant: {
    icon: UtensilsCrossed,
    surface: 'bg-orange-600 bg-linear-to-br from-orange-500 via-red-500 to-rose-700',
  },
  bar: {
    icon: Beer,
    surface: 'bg-violet-600 bg-linear-to-br from-violet-500 via-purple-600 to-indigo-800',
  },
  bakery: {
    icon: Croissant,
    surface: 'bg-yellow-600 bg-linear-to-br from-yellow-400 via-amber-500 to-orange-700',
  },
  park: {
    icon: Trees,
    surface: 'bg-emerald-600 bg-linear-to-br from-emerald-400 via-green-600 to-teal-800',
  },
  playground: {
    icon: ToyBrick,
    surface: 'bg-lime-600 bg-linear-to-br from-lime-400 via-emerald-500 to-green-700',
  },
  museum: {
    icon: Landmark,
    surface: 'bg-stone-600 bg-linear-to-br from-stone-400 via-stone-600 to-neutral-800',
  },
  gallery: {
    icon: Palette,
    surface: 'bg-fuchsia-600 bg-linear-to-br from-fuchsia-400 via-pink-600 to-purple-800',
  },
  landmark: {
    icon: Landmark,
    surface: 'bg-amber-700 bg-linear-to-br from-amber-500 via-yellow-700 to-stone-800',
  },
  theatre: {
    icon: Drama,
    surface: 'bg-rose-600 bg-linear-to-br from-rose-500 via-red-600 to-rose-900',
  },
  cinema: {
    icon: Film,
    surface: 'bg-slate-700 bg-linear-to-br from-slate-600 via-indigo-800 to-slate-900',
  },
  bookstore: {
    icon: BookOpen,
    surface: 'bg-teal-700 bg-linear-to-br from-teal-500 via-cyan-700 to-slate-800',
  },
  library: {
    icon: Library,
    surface: 'bg-cyan-700 bg-linear-to-br from-cyan-500 via-teal-700 to-slate-800',
  },
  shopping: {
    icon: ShoppingBag,
    surface: 'bg-pink-600 bg-linear-to-br from-pink-500 via-rose-600 to-fuchsia-800',
  },
  shopping_mall: {
    icon: ShoppingBag,
    surface: 'bg-pink-600 bg-linear-to-br from-pink-500 via-rose-600 to-fuchsia-800',
  },
  clothes: {
    icon: Shirt,
    surface: 'bg-rose-500 bg-linear-to-br from-rose-400 via-pink-500 to-purple-700',
  },
  supermarket: {
    icon: ShoppingCart,
    surface: 'bg-sky-600 bg-linear-to-br from-sky-500 via-blue-600 to-indigo-800',
  },
  convenience: {
    icon: Store,
    surface: 'bg-blue-600 bg-linear-to-br from-blue-500 via-sky-600 to-cyan-800',
  },
  market: {
    icon: Warehouse,
    surface: 'bg-amber-600 bg-linear-to-br from-amber-500 via-lime-600 to-emerald-800',
  },
  electronics: {
    icon: Smartphone,
    surface: 'bg-zinc-700 bg-linear-to-br from-zinc-500 via-slate-700 to-zinc-900',
  },
  hotel: {
    icon: Hotel,
    surface: 'bg-indigo-600 bg-linear-to-br from-indigo-500 via-violet-600 to-slate-900',
  },
  school: {
    icon: School,
    surface: 'bg-sky-700 bg-linear-to-br from-sky-500 via-indigo-600 to-blue-900',
  },
  university: {
    icon: GraduationCap,
    surface: 'bg-blue-800 bg-linear-to-br from-blue-600 via-indigo-800 to-slate-900',
  },
  hospital: {
    icon: Stethoscope,
    surface: 'bg-red-600 bg-linear-to-br from-red-400 via-rose-600 to-red-900',
  },
  pharmacy: {
    icon: Pill,
    surface: 'bg-emerald-600 bg-linear-to-br from-emerald-400 via-teal-600 to-cyan-800',
  },
  gym: {
    icon: Dumbbell,
    surface: 'bg-slate-700 bg-linear-to-br from-slate-500 via-zinc-700 to-neutral-900',
  },
  bank: {
    icon: Banknote,
    surface: 'bg-emerald-700 bg-linear-to-br from-emerald-500 via-green-700 to-slate-900',
  },
  atm: {
    icon: CreditCard,
    surface: 'bg-teal-700 bg-linear-to-br from-teal-500 via-emerald-700 to-slate-900',
  },
  fuel: {
    icon: Fuel,
    surface: 'bg-orange-700 bg-linear-to-br from-orange-500 via-amber-700 to-stone-900',
  },
  office: {
    icon: Building2,
    surface: 'bg-slate-600 bg-linear-to-br from-slate-500 via-slate-700 to-slate-900',
  },
};

const DEFAULT_STYLE: CoverStyle = {
  icon: MapPin,
  surface: 'bg-emerald-700 bg-linear-to-br from-emerald-600 via-teal-700 to-slate-900',
};

function initialOf(name: string): string {
  // Array.from chứ không phải name[0]: tên tiếng Việt có dấu tổ hợp, cắt theo
  // mã đơn vị UTF-16 có thể lấy ra nửa ký tự và hiện thành ô vuông.
  const first = Array.from(name.trim())[0];
  return first ? first.toLocaleUpperCase('vi-VN') : '?';
}

export function PoiCover({
  name,
  category,
  className,
}: {
  name: string;
  category: string;
  className?: string;
}) {
  const style = COVER_STYLES[category] ?? DEFAULT_STYLE;
  const Icon = style.icon;

  return (
    <div
      role="img"
      aria-label={`Ảnh bìa sinh sẵn cho ${name}`}
      className={cn(
        'relative isolate flex size-full items-center justify-center overflow-hidden',
        style.surface,
        className,
      )}
    >
      <div className="pointer-events-none absolute -right-12 -top-14 size-44 rounded-full bg-white/10" />
      <div className="pointer-events-none absolute -bottom-20 -left-10 size-56 rounded-full bg-white/[0.07]" />
      <Icon
        aria-hidden
        strokeWidth={1.1}
        className="pointer-events-none absolute -bottom-5 -right-4 size-36 text-white/15"
      />
      <span className="relative grid size-16 place-items-center rounded-2xl bg-white/20 text-3xl font-bold text-white ring-1 ring-white/30 backdrop-blur-sm">
        {initialOf(name)}
      </span>
    </div>
  );
}
