/**
 * locale 协商链测试 — README §6.18 / i18n.md §3.3。
 * 优先级:请求显式参数(?locale= / Accept-Language)→ users.settings.locale
 * → workspaces.settings.default_locale → 回退 en;非法值忽略且绝不抛出。
 */
import { describe, expect, it } from 'vitest';
import {
  FALLBACK_LOCALE,
  SUPPORTED_LOCALES,
  matchSupported,
  negotiateLocale,
  parseAcceptLanguage,
} from '../negotiate';

describe('parseAcceptLanguage(§3.3:q 值降序候选)', () => {
  it('按 q 值降序排列候选', () => {
    expect(parseAcceptLanguage('zh-CN,zh;q=0.9,en;q=0.8')).toEqual(['zh-CN', 'zh', 'en']);
  });

  it('乱序 q 值重排;q 相同时保持出现顺序(稳定)', () => {
    expect(parseAcceptLanguage('en;q=0.5,zh-CN;q=0.9,zh;q=0.8')).toEqual(['zh-CN', 'zh', 'en']);
    expect(parseAcceptLanguage('en,zh-CN')).toEqual(['en', 'zh-CN']);
  });

  it('缺省 q=1 优先于显式低 q', () => {
    expect(parseAcceptLanguage('en;q=0.8,fr')).toEqual(['fr', 'en']);
  });

  it('忽略 * 通配项', () => {
    expect(parseAcceptLanguage('*')).toEqual([]);
    expect(parseAcceptLanguage('zh-CN,*;q=0.1,en')).toEqual(['zh-CN', 'en']);
  });

  it('剔除非法标签(非 BCP-47 语法)', () => {
    expect(parseAcceptLanguage('123,zh-CN,en_US')).toEqual(['zh-CN']);
  });

  it('剔除非法 q 值(非数字 / 越界)', () => {
    expect(parseAcceptLanguage('en;q=abc,zh-CN')).toEqual(['zh-CN']);
    expect(parseAcceptLanguage('en;q=1.5,fr;q=-0.1,zh-CN')).toEqual(['zh-CN']);
  });

  it('q=0 表示不接受,剔除(RFC 9110)', () => {
    expect(parseAcceptLanguage('en;q=0,zh-CN')).toEqual(['zh-CN']);
  });

  it('空串 / 噪声输入返回空候选,绝不抛出', () => {
    expect(parseAcceptLanguage('')).toEqual([]);
    expect(parseAcceptLanguage(';;;')).toEqual([]);
    expect(parseAcceptLanguage('   ')).toEqual([]);
  });

  it('容忍条目内空白', () => {
    expect(parseAcceptLanguage(' zh-CN , en ; q=0.7 ')).toEqual(['zh-CN', 'en']);
  });

  it('非字符串输入返回空候选(绝不抛出)', () => {
    expect(parseAcceptLanguage(undefined as unknown as string)).toEqual([]);
    expect(parseAcceptLanguage(42 as unknown as string)).toEqual([]);
  });
});

describe('matchSupported(§3.3:BCP-47 精确 → 语言主干回退)', () => {
  it('精确匹配(大小写不敏感)', () => {
    expect(matchSupported(['zh-cn'], ['zh-CN', 'en'])).toBe('zh-CN');
    expect(matchSupported(['EN'], ['zh-CN', 'en'])).toBe('en');
  });

  it('按候选顺序取首个命中', () => {
    expect(matchSupported(['en', 'zh-CN'], ['zh-CN', 'en'])).toBe('en');
  });

  it('语言主干回退:zh-TW → 同语言受支持区域变体 zh-CN', () => {
    expect(matchSupported(['zh-TW'], ['zh-CN', 'en'])).toBe('zh-CN');
    expect(matchSupported(['ZH-tw'], ['zh-CN', 'en'])).toBe('zh-CN');
  });

  it('纯语言主干候选命中区域变体:zh → zh-CN', () => {
    expect(matchSupported(['zh'], ['zh-CN', 'en'])).toBe('zh-CN');
  });

  it('主干回退优先于下一候选的精确匹配(逐候选处理)', () => {
    expect(matchSupported(['en-GB', 'fr'], ['fr', 'en'])).toBe('en');
  });

  it('全部不中返回 null', () => {
    expect(matchSupported(['ja', 'ko'], ['zh-CN', 'en'])).toBeNull();
    expect(matchSupported([], ['zh-CN', 'en'])).toBeNull();
    expect(matchSupported(['zh-CN'], [])).toBeNull();
  });

  it('空白 / 非法候选跳过,绝不抛出', () => {
    expect(matchSupported(['', '  ', '!!'], ['zh-CN', 'en'])).toBeNull();
    expect(matchSupported(['  zh-CN  '], ['zh-CN', 'en'])).toBe('zh-CN');
    expect(matchSupported(['zh-CN', null as unknown as string], ['zh-CN'])).toBe('zh-CN');
  });
});

describe('negotiateLocale(§6.18 协商链)', () => {
  it('首发受支持清单为 zh-CN + en,回退为 en', () => {
    expect(SUPPORTED_LOCALES).toEqual(['zh-CN', 'en']);
    expect(FALLBACK_LOCALE).toBe('en');
  });

  it('请求显式参数优先于用户偏好与工作区默认', () => {
    expect(
      negotiateLocale({ requested: 'zh-CN', userLocale: 'en', workspaceDefaultLocale: 'en' }),
    ).toBe('zh-CN');
  });

  it('requested 作为 Accept-Language 头解析(含 q 值与主干回退)', () => {
    expect(negotiateLocale({ requested: 'zh-TW,zh;q=0.9,en;q=0.8' })).toBe('zh-CN');
    expect(negotiateLocale({ requested: 'fr,de;q=0.9', userLocale: 'zh-CN' })).toBe('zh-CN');
  });

  it('requested 作为候选数组解析', () => {
    expect(negotiateLocale({ requested: ['fr', 'zh-CN'] })).toBe('zh-CN');
  });

  it('requested 为 null / 空串时落到用户偏好', () => {
    expect(negotiateLocale({ requested: null, userLocale: 'en-US' })).toBe('en');
    expect(negotiateLocale({ requested: '', userLocale: 'zh-CN' })).toBe('zh-CN');
  });

  it('userLocale 为 null 时跳过本级,落到工作区默认(§6.18)', () => {
    expect(
      negotiateLocale({ requested: null, userLocale: null, workspaceDefaultLocale: 'zh-CN' }),
    ).toBe('zh-CN');
  });

  it('全部缺省时回退 en', () => {
    expect(negotiateLocale({})).toBe('en');
    expect(
      negotiateLocale({ requested: null, userLocale: null, workspaceDefaultLocale: null }),
    ).toBe('en');
  });

  it('非法值忽略并继续协商,绝不返回 400/抛出(§3.3)', () => {
    expect(
      negotiateLocale({ requested: '!!!', userLocale: '????', workspaceDefaultLocale: 'zh-CN' }),
    ).toBe('zh-CN');
    expect(
      negotiateLocale({ requested: '!!!', userLocale: '????', workspaceDefaultLocale: '###' }),
    ).toBe('en');
    expect(negotiateLocale({ requested: 12345 as unknown as string })).toBe('en');
  });

  it('自定义 supported 与 fallback', () => {
    expect(negotiateLocale({ requested: 'fr-FR', supported: ['fr'], fallback: 'fr' })).toBe('fr');
    expect(negotiateLocale({ requested: 'de', supported: ['fr'], fallback: 'fr' })).toBe('fr');
  });

  it('工作区默认不在受支持清单时忽略并回退', () => {
    expect(negotiateLocale({ workspaceDefaultLocale: 'ja-JP' })).toBe('en');
  });
});

describe('negotiateLocale systemLocales(系统级候选,Accept-Language 的 SPA 等价物)', () => {
  it('账号偏好与工作区默认皆无时,尝试浏览器语言', () => {
    expect(negotiateLocale({ systemLocales: ['zh-CN', 'en'] })).toBe('zh-CN');
  });

  it('账号偏好优先于浏览器语言(否则账号级偏好永不生效)', () => {
    expect(
      negotiateLocale({ userLocale: 'en', systemLocales: ['zh-CN'] }),
    ).toBe('en');
  });

  it('工作区默认优先于浏览器语言', () => {
    expect(
      negotiateLocale({ workspaceDefaultLocale: 'en', systemLocales: ['zh-CN'] }),
    ).toBe('en');
  });

  it('显式请求参数仍为最高优先', () => {
    expect(
      negotiateLocale({ requested: 'zh-CN', userLocale: 'en', systemLocales: ['en'] }),
    ).toBe('zh-CN');
  });

  it('浏览器语言不受支持 → 回退 en', () => {
    expect(negotiateLocale({ systemLocales: ['fr-FR', 'de'] })).toBe('en');
  });
});
