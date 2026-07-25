/**
 * App 组装入口(README §6.12 / Task 5):Provider 树 + 路由。
 *
 * BrowserRouter > ThemeProvider > I18nProvider > ToastProvider > ShortcutProvider。
 * useT 须在 I18nProvider 内,故内层 ShellProviders 负责取本地化文案并喂给
 * ToastProvider(regionLabel)与命令面板/帮助层;二者的 open 态以 useState 持有于本层,
 * ShortcutProvider 回调开启,OverlayControls 下达 TopBar。路由树经 ErrorBoundary 兜底。
 */
import { useCallback, useMemo, useState } from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, ToastProvider } from './design';
import { I18nProvider, useT } from './i18n';
import { CommandPalette, ShortcutHelp, ShortcutProvider } from './shortcuts';
import { AppShell, OverlayControlsProvider } from './shell/AppShell';
import type { OverlayControls } from './shell/AppShell';
import { PlaceholderPage } from './shell/PlaceholderPage';
import { ErrorBoundary } from './shell/pages/ErrorPage';
import { HomePage } from './shell/pages/HomePage';
import { LoginPage } from './shell/pages/LoginPage';
import { NotFoundPage } from './shell/pages/NotFoundPage';
import { SettingsPage } from './shell/pages/SettingsPage';

export default function App(): React.JSX.Element {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null}>
          <ShellProviders />
        </I18nProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}

function ShellProviders(): React.JSX.Element {
  const t = useT();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  const controls = useMemo<OverlayControls>(
    () => ({
      openPalette: () => setPaletteOpen(true),
      openHelp: () => setHelpOpen(true),
    }),
    [],
  );
  const closePalette = useCallback(() => setPaletteOpen(false), []);
  const closeHelp = useCallback(() => setHelpOpen(false), []);

  return (
    <ToastProvider regionLabel={t('a11y.notifications')}>
      <ShortcutProvider onOpenPalette={controls.openPalette} onOpenHelp={controls.openHelp}>
        <OverlayControlsProvider value={controls}>
          <ErrorBoundary>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/" element={<AppShell />}>
                <Route index element={<HomePage />} />
                <Route path="settings" element={<SettingsPage />} />
                <Route path="inbox" element={<PlaceholderPage kind="inbox" />} />
                <Route path="projects" element={<PlaceholderPage kind="projects" />} />
                <Route path="board" element={<PlaceholderPage kind="board" />} />
                <Route path="members" element={<PlaceholderPage kind="members" />} />
                <Route path="chat" element={<PlaceholderPage kind="chat" />} />
                <Route path="automation" element={<PlaceholderPage kind="automation" />} />
              </Route>
              <Route path="*" element={<NotFoundPage />} />
            </Routes>
          </ErrorBoundary>
          <CommandPalette
            open={paletteOpen}
            onClose={closePalette}
            title={t('shortcuts.paletteTitle')}
            closeLabel={t('a11y.closeDialog')}
            searchPlaceholder={t('shortcuts.palettePlaceholder')}
            emptyText={t('shortcuts.paletteEmpty')}
          />
          <ShortcutHelp
            open={helpOpen}
            onClose={closeHelp}
            title={t('shortcuts.helpTitle')}
            closeLabel={t('a11y.closeDialog')}
            groupLabels={{
              global: t('shortcuts.groupGlobal'),
              board: t('shortcuts.groupBoard'),
              issue: t('shortcuts.groupIssue'),
              chat: t('shortcuts.groupChat'),
            }}
          />
        </OverlayControlsProvider>
      </ShortcutProvider>
    </ToastProvider>
  );
}
