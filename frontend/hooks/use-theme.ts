'use client';

import { useCallback, useEffect, useState } from 'react';

type Theme = 'light' | 'dark';

const STORAGE_KEY = 'nearby-theme';

function readStoredTheme(): Theme | null {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === 'dark' || stored === 'light' ? stored : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark');
}

export function useTheme() {
  // Khởi tạo khớp với HTML mà server render ('light') để lần render đầu ở
  // client không lệch -> tránh hydration mismatch. Theme thật (localStorage/hệ
  // thống) được áp sau khi mount; script chặn-flash ở layout đã set sẵn class
  // '.dark' trên <html> trước hydration nên không nháy màu.
  const [theme, setTheme] = useState<Theme>('light');
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setTheme(readStoredTheme() ?? systemTheme());
    setMounted(true);
  }, []);

  useEffect(() => {
    // Chỉ áp sau khi mount để không gỡ nhầm class '.dark' do script layout đặt.
    if (mounted) applyTheme(theme);
  }, [theme, mounted]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === 'dark' ? 'light' : 'dark';
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // localStorage có thể bị chặn (chế độ ẩn danh) — không ảnh hưởng chức năng.
      }
      return next;
    });
  }, []);

  return { theme, toggleTheme, mounted };
}
