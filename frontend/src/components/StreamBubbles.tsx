import { useSessionStore } from '../stores/sessionStore';
import { SafeMarkdown } from './SafeMarkdown';
import { useI18n } from '../i18n';

export function StreamBubbles() {
  const streams = useSessionStore((state) => state.streams);
  const { t } = useI18n();
  const entries = Object.entries(streams).filter(([, text]) => text.trim());
  if (!entries.length) return null;
  return (
    <div className="space-y-6" aria-live="polite" aria-label={t('streaming', { model: entries.map(([name]) => name).join('、') })}>
      {entries.map(([model, text]) => (
        <article key={model} className="block" data-testid="stream-bubble">
          <div className="mb-1.5 text-[12px] text-[#8b8176]">{model}</div>
          <div className="text-[15px] leading-7 text-[#2f2a24]">
            <SafeMarkdown content={text} />
          </div>
        </article>
      ))}
    </div>
  );
}
