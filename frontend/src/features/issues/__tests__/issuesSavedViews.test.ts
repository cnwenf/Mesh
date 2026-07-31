/**
 * issuesSavedViews 纯助手单测:解析校验 / 存储降级 / 上限 / 增删覆盖。
 */
import { describe, expect, it, vi } from 'vitest';
import {
  MAX_SAVED_VIEWS,
  SAVED_VIEWS_STORAGE_KEY,
  isSavedView,
  loadSavedViews,
  parseSavedViews,
  persistSavedViews,
  removeSavedView,
  safeLocalStorage,
  upsertSavedView,
} from '../issuesSavedViews';
import type { SavedView } from '../issuesSavedViews';

/** 内存 Storage 桩(可选 setItem 抛错以模拟隐私模式)。 */
function makeStorage(
  initial: Record<string, string> = {},
  opts: { throwOnSet?: boolean } = {},
): Storage {
  const store: Record<string, string> = { ...initial };
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      if (opts.throwOnSet === true) throw new Error('quota');
      store[key] = String(value);
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      for (const key of Object.keys(store)) delete store[key];
    },
    key: (index: number) => Object.keys(store)[index] ?? null,
    get length() {
      return Object.keys(store).length;
    },
  };
}

function view(name: string, params: Record<string, string> = {}): SavedView {
  return { name, params };
}

describe('isSavedView', () => {
  it('接受合法结构', () => {
    expect(isSavedView({ name: 'My', params: { q: 'x' } })).toBe(true);
    expect(isSavedView({ name: 'My', params: {} })).toBe(true);
  });

  it('拒绝非法结构', () => {
    expect(isSavedView(null)).toBe(false);
    expect(isSavedView('x')).toBe(false);
    expect(isSavedView({ name: '', params: {} })).toBe(false);
    expect(isSavedView({ name: '  ', params: {} })).toBe(false);
    expect(isSavedView({ name: 'x' })).toBe(false);
    expect(isSavedView({ name: 'x', params: [] })).toBe(false);
    expect(isSavedView({ name: 'x', params: { a: 1 } })).toBe(false);
    expect(isSavedView({ name: 'x', params: null })).toBe(false);
  });
});

describe('parseSavedViews', () => {
  it('null / 空串 / 畸形 JSON / 非数组回退空列表', () => {
    expect(parseSavedViews(null)).toEqual([]);
    expect(parseSavedViews('')).toEqual([]);
    expect(parseSavedViews('{not json')).toEqual([]);
    expect(parseSavedViews('{"a":1}')).toEqual([]);
    expect(parseSavedViews('"str"')).toEqual([]);
  });

  it('过滤非法项并保留合法项', () => {
    const raw = JSON.stringify([{ name: 'ok', params: { q: 'a' } }, { bad: true }, 42]);
    expect(parseSavedViews(raw)).toEqual([{ name: 'ok', params: { q: 'a' } }]);
  });

  it('截断到上限', () => {
    const many = Array.from({ length: MAX_SAVED_VIEWS + 5 }, (_, i) => view(`v${i}`));
    const parsed = parseSavedViews(JSON.stringify(many));
    expect(parsed.length).toBe(MAX_SAVED_VIEWS);
    expect(parsed[0].name).toBe('v0');
  });
});

describe('loadSavedViews', () => {
  it('storage 为 null 返回空列表', () => {
    expect(loadSavedViews(null)).toEqual([]);
  });

  it('读取并解析已存项', () => {
    const storage = makeStorage({
      [SAVED_VIEWS_STORAGE_KEY]: JSON.stringify([{ name: 'a', params: { mine: 'true' } }]),
    });
    expect(loadSavedViews(storage)).toEqual([{ name: 'a', params: { mine: 'true' } }]);
  });

  it('畸形 JSON 回退空列表', () => {
    const storage = makeStorage({ [SAVED_VIEWS_STORAGE_KEY]: 'oops' });
    expect(loadSavedViews(storage)).toEqual([]);
  });

  it('getItem 抛错回退空列表', () => {
    const storage = makeStorage();
    storage.getItem = () => {
      throw new Error('denied');
    };
    expect(loadSavedViews(storage)).toEqual([]);
  });

  it('默认参数经 safeLocalStorage 读取真实存储', () => {
    window.localStorage.setItem(SAVED_VIEWS_STORAGE_KEY, JSON.stringify([view('w')]));
    expect(loadSavedViews()).toEqual([view('w')]);
    window.localStorage.removeItem(SAVED_VIEWS_STORAGE_KEY);
  });
});

describe('persistSavedViews', () => {
  it('写入 JSON', () => {
    const storage = makeStorage();
    persistSavedViews([view('a', { q: 'x' })], storage);
    expect(JSON.parse(storage.getItem(SAVED_VIEWS_STORAGE_KEY) ?? '[]')).toEqual([
      { name: 'a', params: { q: 'x' } },
    ]);
  });

  it('storage 为 null 或写入抛错均不抛异常', () => {
    expect(() => persistSavedViews([view('a')], null)).not.toThrow();
    expect(() => persistSavedViews([view('a')], makeStorage({}, { throwOnSet: true }))).not.toThrow();
  });

  it('写入前截断到上限', () => {
    const storage = makeStorage();
    const many = Array.from({ length: MAX_SAVED_VIEWS + 3 }, (_, i) => view(`v${i}`));
    persistSavedViews(many, storage);
    const stored = JSON.parse(storage.getItem(SAVED_VIEWS_STORAGE_KEY) ?? '[]') as SavedView[];
    expect(stored.length).toBe(MAX_SAVED_VIEWS);
  });
});

describe('upsertSavedView', () => {
  it('追加并裁剪名称空白', () => {
    expect(upsertSavedView([], view('  Mine  ', { mine: 'true' }))).toEqual([
      { name: 'Mine', params: { mine: 'true' } },
    ]);
  });

  it('同名覆盖(不新增条目)', () => {
    const result = upsertSavedView([view('a', { q: '1' })], view('a', { q: '2' }));
    expect(result).toEqual([{ name: 'a', params: { q: '2' } }]);
  });

  it('超出上限丢弃最旧项', () => {
    const full = Array.from({ length: MAX_SAVED_VIEWS }, (_, i) => view(`v${i}`));
    const result = upsertSavedView(full, view('new'));
    expect(result.length).toBe(MAX_SAVED_VIEWS);
    expect(result[result.length - 1].name).toBe('new');
    expect(result.some((v) => v.name === 'v0')).toBe(false);
  });

  it('不可变:不修改入参数组', () => {
    const original = [view('a')];
    upsertSavedView(original, view('b'));
    expect(original).toEqual([view('a')]);
  });
});

describe('removeSavedView', () => {
  it('按名删除且不可变', () => {
    const original = [view('a'), view('b')];
    const result = removeSavedView(original, 'a');
    expect(result).toEqual([view('b')]);
    expect(original.length).toBe(2);
  });
});

describe('safeLocalStorage', () => {
  it('正常环境返回 localStorage', () => {
    expect(safeLocalStorage()).toBe(window.localStorage);
  });

  it('window 访问抛错时返回 null', () => {
    const spy = vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(safeLocalStorage()).toBeNull();
    spy.mockRestore();
  });
});
