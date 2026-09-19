'use client';

import { useState, useSyncExternalStore } from 'react';
import {
  BookOpen,
  Compass,
  GraduationCap,
  Layers,
  UserRound,
  Users,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

const STORAGE_KEY = 'nearby-about-seen';

const MEMBERS = [
  { name: 'Tăng Ngọc Phụng', id: 'KHMT836027' },
  { name: 'Hoàng Châu Ngọc Phương', id: 'KHMT836028' },
  { name: 'Lê Thị Mai Len', id: 'KHMT836015' },
];

const FEATURES = [
  'Tìm kiếm địa điểm theo vị trí, bán kính, danh mục và đánh giá tối thiểu',
  'Truy xuất đa kênh BM25 · geo · vector, hợp nhất bằng Reciprocal Rank Fusion',
  'Làm giàu ngữ cảnh không gian – thời gian: giờ mở cửa, độ đông theo khung giờ, ETA',
  'Gợi ý cá nhân hoá từ Spatial Knowledge Graph (Neo4j) và Feature Store',
  'Chỉ đường thật bằng OSRM cho xe máy, ô tô, đi bộ',
  'Nhắc khi tới gần địa điểm (Service Worker + thông báo đẩy)',
];

const STACK = [
  'Next.js / React',
  'MapLibre GL',
  'FastAPI',
  'PostGIS',
  'OpenSearch',
  'Neo4j',
  'Redis',
  'H3',
  'OSRM',
  'Docker',
];

export function AboutDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] gap-5 overflow-y-auto p-0 sm:max-w-2xl [&>[data-slot=dialog-close]]:text-white [&>[data-slot=dialog-close]]:hover:bg-white/15">
        <div className="rounded-t-xl bg-gradient-to-br from-emerald-700 to-emerald-500 px-6 pt-6 pb-5 text-white">
          <div className="flex items-center gap-3">
            <div className="grid size-12 shrink-0 place-items-center rounded-2xl bg-white/15 ring-1 ring-white/25">
              <GraduationCap className="size-6" />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-semibold tracking-wide uppercase opacity-90">
                Trường Đại học Sư phạm Thành phố Hồ Chí Minh
              </p>
              <p className="text-sm opacity-90">Khoa Công nghệ thông tin</p>
            </div>
          </div>
          <DialogHeader className="mt-5 gap-1">
            <DialogTitle className="text-2xl leading-tight font-bold text-white">
              Nearby — Hệ thống gợi ý địa điểm theo vị trí
            </DialogTitle>
            <DialogDescription className="text-white/85">
              Đồ án tìm kiếm, xếp hạng và gợi ý địa điểm quanh người dùng tại
              TP. Hồ Chí Minh.
            </DialogDescription>
          </DialogHeader>
        </div>

        <div className="grid gap-5 px-6 pb-2">
          <div className="grid gap-4 sm:grid-cols-[5fr_7fr]">
            <section className="rounded-xl border bg-muted/40 p-4">
              <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                <UserRound className="size-4" />
                Giảng viên hướng dẫn
              </h3>
              <p className="text-base font-semibold">TS.GVC. Nguyễn Quốc Huy</p>
            </section>
            <section className="rounded-xl border bg-muted/40 p-4">
              <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                <Users className="size-4" />
                Học viên thực hiện
              </h3>
              <ol className="grid gap-1.5">
                {MEMBERS.map((member, index) => (
                  <li
                    key={member.id}
                    className="flex flex-wrap items-baseline justify-between gap-x-3"
                  >
                    <span className="font-medium whitespace-nowrap">
                      {index + 1}. {member.name}
                    </span>
                    <span className="font-mono text-xs text-muted-foreground tabular-nums">
                      {member.id}
                    </span>
                  </li>
                ))}
              </ol>
            </section>
          </div>

          <section>
            <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              <BookOpen className="size-4" />
              Chức năng chính
            </h3>
            <ul className="grid gap-1.5">
              {FEATURES.map((feature) => (
                <li key={feature} className="flex gap-2">
                  <Compass className="mt-0.5 size-4 shrink-0 text-primary" />
                  <span>{feature}</span>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              <Layers className="size-4" />
              Công nghệ
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {STACK.map((item) => (
                <Badge key={item} variant="secondary">
                  {item}
                </Badge>
              ))}
            </div>
          </section>
        </div>

        <DialogFooter className="mx-0 mb-0 rounded-b-xl px-6 py-4">
          <Button onClick={() => onOpenChange(false)}>Bắt đầu khám phá</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function readSeen() {
  try {
    return localStorage.getItem(STORAGE_KEY) !== null;
  } catch {
    // Chế độ ẩn danh / bị chặn storage: coi như đã xem, không tự mở.
    return true;
  }
}

const noopSubscribe = () => () => {};

// Mở hộp giới thiệu ở lần truy cập đầu tiên (tiện khi trình bày đồ án), các
// lần sau chỉ mở khi người dùng bấm nút "Giới thiệu". Snapshot phía server là
// "đã xem" để HTML server render khớp với lần hydrate đầu.
export function useAboutDialog() {
  const seen = useSyncExternalStore(noopSubscribe, readSeen, () => true);
  const [dismissed, setDismissed] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);

  const open = manualOpen || (!seen && !dismissed);

  const onOpenChange = (next: boolean) => {
    setManualOpen(next);
    if (!next) {
      setDismissed(true);
      try {
        localStorage.setItem(STORAGE_KEY, '1');
      } catch {
        // Không lưu được thì lần sau hộp lại tự mở, không sao.
      }
    }
  };

  return { open, setOpen: setManualOpen, onOpenChange };
}
