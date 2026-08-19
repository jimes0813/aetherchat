import { useEffect, useRef, type RefObject } from 'react';

const NEAR_BOTTOM_PX = 96;

export function isNearBottom(scroller: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>) {
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < NEAR_BOTTOM_PX;
}

export function useStickToBottom(
  scrollerRef: RefObject<HTMLElement | null>,
  ...identity: unknown[]
) {
  const stickRef = useRef(true);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return undefined;
    const onScroll = () => {
      stickRef.current = isNearBottom(scroller);
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    return () => scroller.removeEventListener('scroll', onScroll);
  }, [scrollerRef]);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller || typeof ResizeObserver === 'undefined') return undefined;
    const stickIfNeeded = () => {
      if (stickRef.current) scroller.scrollTop = scroller.scrollHeight;
    };
    const observer = new ResizeObserver(stickIfNeeded);
    observer.observe(scroller);
    if (scroller.firstElementChild) observer.observe(scroller.firstElementChild);
    return () => observer.disconnect();
  }, [scrollerRef]);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (scroller && stickRef.current) scroller.scrollTop = scroller.scrollHeight;
  }, [scrollerRef, ...identity]);

  return {
    release() {
      stickRef.current = false;
    },
  };
}
