import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../stores/sessionStore', () => ({
  useSessionStore: (selector: (state: {
    messages: unknown[];
    settings: { uiLocale: string };
  }) => unknown) =>
    selector({ messages: [], settings: { uiLocale: 'zh-CN' } }),
}));

vi.mock('./StreamBubbles', () => ({ StreamBubbles: () => null }));
vi.mock('./ChatEmptyState', () => ({
  ChatEmptyState: () => <div>empty-feed</div>,
}));

import { UnifiedFeed } from './UnifiedFeed';

describe('UnifiedFeed virtual shell', () => {
  it('renders empty state without crashing', () => {
    render(<UnifiedFeed hasConfiguredModel />);
    expect(screen.getByText('empty-feed')).toBeTruthy();
  });
});
