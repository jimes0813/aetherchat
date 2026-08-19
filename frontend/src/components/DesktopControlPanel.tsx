import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import {
  Camera,
  Check,
  CircleStop,
  Crosshair,
  Eye,
  Keyboard,
  Laptop,
  MousePointer2,
  Radar,
  ScanLine,
  ShieldCheck,
  Sparkles,
  ZoomIn,
} from 'lucide-react';
import { useI18n } from '../i18n';
import type {
  ComputerAction,
  ComputerActionOptions,
  ComputerActionReceipt,
  ComputerPermissionMode,
  ComputerScreenshot,
} from '../types/desktop';
import { DesktopDocumentPanel } from './DesktopDocumentPanel';
import {
  ACTION_LABELS,
  PERMISSION_MODES,
  timelineTimestamp,
  type TimelineItem,
} from './desktopControlModel';

export function DesktopControlPanel() {
  const { ui } = useI18n();
  const bridge = window.aetherDesktop;
  const imageRef = useRef<HTMLImageElement>(null);
  const historyCursorRef = useRef(0);
  const [capabilities, setCapabilities] = useState<Record<string, unknown> | null>(null);
  const [permissionMode, setPermissionMode] = useState<ComputerPermissionMode>('manual');
  const [screenshot, setScreenshot] = useState<ComputerScreenshot | null>(null);
  const [zoomScreenshot, setZoomScreenshot] = useState<ComputerScreenshot | null>(null);
  const [coordinate, setCoordinate] = useState<[number, number] | null>(null);
  const [dragStart, setDragStart] = useState<[number, number] | null>(null);
  const [mouseX, setMouseX] = useState('');
  const [mouseY, setMouseY] = useState('');
  const [textInput, setTextInput] = useState('');
  const [keyInput, setKeyInput] = useState('ctrl+l');
  const [scrollAmount, setScrollAmount] = useState('600');
  const [actionArmed, setActionArmed] = useState(false);
  const [busy, setBusy] = useState('');
  const [activeAction, setActiveAction] = useState<ComputerAction | null>(null);
  const [observedAction, setObservedAction] = useState<ComputerAction | null>(null);
  const [status, setStatus] = useState('');
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [effect, setEffect] = useState<{ id: string; action: ComputerAction; point: [number, number] } | null>(null);

  const computerUseAvailable = capabilities?.model_computer_use === true;
  const mouseAvailable = capabilities?.mouse === true;
  const protocol = String(capabilities?.computer_protocol || 'computer_20251124');
  const canControl = mouseAvailable
    && permissionMode !== 'observe'
    && (permissionMode === 'auto' || actionArmed);
  const visibleActiveAction = activeAction || observedAction;

  useEffect(() => {
    if (!bridge) {
      setStatus('电脑控制仅在 AetherChat 桌面版中可用');
      return;
    }
    bridge.status()
      .then((value) => {
        setCapabilities(value);
        const mode = value.permission_mode;
        if (mode === 'manual' || mode === 'observe' || mode === 'auto') setPermissionMode(mode);
      })
      .catch((error) => setStatus(`电脑服务不可用：${String(error)}`));
    const unsubscribe = bridge.onComputerEvent?.((event) => {
      if (event.type === 'permission_changed' && event.permissionMode) {
        setPermissionMode(event.permissionMode);
      }
      if (event.type === 'interrupted') {
        setPermissionMode('manual');
        setActiveAction(null);
        setBusy('');
      }
    });
    return () => unsubscribe?.();
  }, [bridge]);

  const updateCoordinate = useCallback((next: [number, number]) => {
    setEffect(null);
    setCoordinate(next);
    setMouseX(String(next[0]));
    setMouseY(String(next[1]));
  }, []);

  const actionCoordinate = useCallback((): [number, number] => {
    const x = Number.parseInt(mouseX, 10);
    const y = Number.parseInt(mouseY, 10);
    if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error('请输入有效的截图 X/Y 坐标');
    if (screenshot && (x < 0 || y < 0 || x >= screenshot.width || y >= screenshot.height)) {
      throw new Error(`坐标必须位于当前 ${screenshot.width}×${screenshot.height} 截图内`);
    }
    return [x, y];
  }, [mouseX, mouseY, screenshot]);

  const cursorInScreenshot = useCallback((
    native: { x: number; y: number },
    image: ComputerScreenshot | null,
  ): [number, number] => {
    if (!image) return [native.x, native.y];
    return [
      Math.max(0, Math.min(image.width - 1, Math.round((native.x - image.origin_x) / image.scale_x))),
      Math.max(0, Math.min(image.height - 1, Math.round((native.y - image.origin_y) / image.scale_y))),
    ];
  }, []);

  useEffect(() => {
    if (!bridge?.computerHistory) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const history = await bridge.computerHistory(historyCursorRef.current);
        if (disposed) return;
        historyCursorRef.current = Math.max(historyCursorRef.current, history.latest_sequence);
        if (history.events.length) {
          setTimeline((items) => {
            const next = [...items];
            for (const event of history.events) {
              const timelineItem: TimelineItem = {
                id: event.action_id,
                action: event.action,
                label: ACTION_LABELS[event.action],
                phase: event.phase,
                at: event.at,
                durationMs: event.duration_ms,
                detail: event.detail === 'post_action_screenshot'
                  ? '截图已复核'
                  : event.detail === 'observation' ? '观察完成' : event.detail,
              };
              const existing = next.findIndex((item) => item.id === event.action_id);
              if (existing >= 0) next[existing] = { ...next[existing], ...timelineItem };
              else next.unshift(timelineItem);
            }
            return next.filter((item, index, values) => (
              values.findIndex((candidate) => candidate.id === item.id) === index
            )).slice(0, 16);
          });
        }
        if (!history.active_action_id) {
          setObservedAction(null);
        } else {
          const activeEvent = [...history.events].reverse().find((event) => (
            event.action_id === history.active_action_id && event.phase === 'running'
          ));
          if (activeEvent) setObservedAction(activeEvent.action);
        }
        const receipt = history.latest_receipt;
        if (receipt?.screenshot) {
          if (receipt.action === 'zoom') {
            setZoomScreenshot(receipt.screenshot);
          } else {
            setScreenshot(receipt.screenshot);
          }
          const cursor = cursorInScreenshot(receipt.cursor_after, receipt.screenshot);
          if (receipt.action !== 'zoom') updateCoordinate(cursor);
          if (['left_click', 'right_click', 'middle_click', 'double_click', 'triple_click'].includes(receipt.action)) {
            setEffect({ id: receipt.action_id, action: receipt.action, point: cursor });
          }
        }
      } catch {
        // The backend can be restarting while the panel remains mounted.
      } finally {
        if (!disposed) timer = setTimeout(poll, 700);
      }
    };
    void poll();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [bridge, cursorInScreenshot, updateCoordinate]);

  const runAction = useCallback(async (
    action: ComputerAction,
    options: ComputerActionOptions = {},
  ): Promise<ComputerActionReceipt | null> => {
    if (!bridge?.computerAction || visibleActiveAction) return null;
    const timelineId = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const label = ACTION_LABELS[action];
    const pendingItem: TimelineItem = {
      id: timelineId,
      action,
      label,
      phase: 'running',
      at: timelineTimestamp(),
      detail: action === 'type' ? `${String(options.text || '').length} chars` : undefined,
    };
    setTimeline((items) => [pendingItem, ...items].slice(0, 16));
    setActiveAction(action);
    setBusy(`computer-${action}`);
    setStatus(`${label} · 执行中`);
    try {
      const receipt = await bridge.computerAction(action, options);
      if (receipt.screenshot) {
        if (action === 'zoom') {
          setZoomScreenshot(receipt.screenshot);
        } else {
          setScreenshot(receipt.screenshot);
          setZoomScreenshot(null);
        }
      }
      const baseImage = action === 'zoom' ? screenshot : (receipt.screenshot || screenshot);
      const nextCursor = cursorInScreenshot(receipt.cursor_after, baseImage);
      updateCoordinate(nextCursor);
      if ([
        'left_click', 'right_click', 'middle_click', 'double_click', 'triple_click',
        'left_mouse_down', 'left_mouse_up',
      ].includes(action)) {
        setEffect({ id: receipt.action_id, action, point: coordinate || nextCursor });
      }
      setTimeline((items) => items.map<TimelineItem>((item) => item.id === timelineId ? {
        ...item,
        id: receipt.action_id,
        phase: 'verified',
        durationMs: receipt.duration_ms,
        detail: receipt.verification === 'post_action_screenshot' ? '截图已复核' : '观察完成',
      } : item).filter((item, index, values) => (
        values.findIndex((candidate) => candidate.id === item.id) === index
      )));
      if (action === 'screenshot' && receipt.screenshot) {
        setStatus(`已截取主屏幕 ${receipt.screenshot.width}×${receipt.screenshot.height}，动作坐标已校准`);
      } else if (action === 'mouse_move') {
        setStatus('鼠标 移动完成');
      } else if (action === 'cursor_position') {
        setStatus(`鼠标位置：(${nextCursor[0]}, ${nextCursor[1]})`);
      } else {
        setStatus(`${label}完成 · ${receipt.duration_ms} ms · 已验证`);
      }
      if (permissionMode === 'manual' && !['screenshot', 'zoom', 'cursor_position', 'wait'].includes(action)) {
        setActionArmed(false);
      }
      return receipt;
    } catch (error) {
      const message = String(error);
      setTimeline((items) => items.map((item) => item.id === timelineId ? {
        ...item,
        phase: message.includes('interrupt') || message.includes('接管') ? 'interrupted' : 'failed',
        detail: message,
      } : item));
      setStatus(`${label}失败：${message}`);
      return null;
    } finally {
      setActiveAction(null);
      setBusy('');
    }
  }, [bridge, coordinate, cursorInScreenshot, permissionMode, screenshot, updateCoordinate, visibleActiveAction]);

  const selectPermissionMode = async (mode: ComputerPermissionMode) => {
    if (!bridge?.setComputerPermission || busy) return;
    try {
      const result = await bridge.setComputerPermission(mode);
      setPermissionMode(result.permissionMode);
      setActionArmed(false);
      setStatus(result.permissionMode === mode
        ? `Computer Use 已切换为${PERMISSION_MODES.find((item) => item.mode === mode)?.label}`
        : '权限模式未改变');
    } catch (error) {
      setStatus(`权限切换失败：${String(error)}`);
    }
  };

  const interruptComputer = async () => {
    if (!bridge?.cancelComputer) return;
    setBusy('interrupt');
    try {
      await bridge.cancelComputer();
      setPermissionMode('manual');
      setActionArmed(false);
      setActiveAction(null);
      const interruptedItem: TimelineItem = {
        id: `interrupt-${Date.now()}`,
        action: 'wait',
        label: '用户接管',
        phase: 'interrupted',
        at: timelineTimestamp(),
        detail: '动作已中断，会话权限已撤销',
      };
      setTimeline((items) => [interruptedItem, ...items].slice(0, 16));
      setStatus('你已接管电脑；进行中动作和 Computer Use 会话权限均已撤销');
    } catch (error) {
      setStatus(`接管失败：${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const handlePreviewClick = (event: ReactMouseEvent<HTMLImageElement>) => {
    if (!screenshot || visibleActiveAction) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = Math.max(0, Math.min(
      screenshot.width - 1,
      Math.round((event.clientX - bounds.left) / bounds.width * screenshot.width),
    ));
    const y = Math.max(0, Math.min(
      screenshot.height - 1,
      Math.round((event.clientY - bounds.top) / bounds.height * screenshot.height),
    ));
    updateCoordinate([x, y]);
    setEffect({ id: `target-${Date.now()}`, action: 'mouse_move', point: [x, y] });
    setStatus(`已选择截图坐标 (${x}, ${y})；尚未执行操作`);
  };

  const zoomAtCoordinate = async () => {
    if (!screenshot) {
      setStatus('请先观察屏幕');
      return;
    }
    try {
      const [x, y] = actionCoordinate();
      const halfWidth = Math.min(220, Math.floor(screenshot.width / 2));
      const halfHeight = Math.min(160, Math.floor(screenshot.height / 2));
      await runAction('zoom', {
        region: [
          Math.max(0, x - halfWidth),
          Math.max(0, y - halfHeight),
          Math.min(screenshot.width - 1, x + halfWidth),
          Math.min(screenshot.height - 1, y + halfHeight),
        ],
      });
    } catch (error) {
      setStatus(String(error));
    }
  };

  const runCoordinateAction = async (action: ComputerAction) => {
    try {
      await runAction(action, { coordinate: actionCoordinate() });
    } catch (error) {
      setStatus(String(error));
    }
  };

  const runDrag = async () => {
    if (!dragStart) {
      setStatus('请先把当前坐标设为拖拽起点');
      return;
    }
    try {
      await runAction('left_click_drag', {
        start_coordinate: dragStart,
        coordinate: actionCoordinate(),
        duration: 0.6,
      });
      setDragStart(null);
    } catch (error) {
      setStatus(String(error));
    }
  };

  const previewEffectStyle = useMemo(() => {
    if (!effect || !screenshot) return undefined;
    return {
      left: `${effect.point[0] / screenshot.width * 100}%`,
      top: `${effect.point[1] / screenshot.height * 100}%`,
    };
  }, [effect, screenshot]);

  const saveScreenshot = async () => {
    if (!bridge || !screenshot) return;
    setBusy('save-shot');
    try {
      const result = await bridge.saveScreenshot(screenshot.image_data_url);
      setStatus(`截图已保存：${result.locationLabel}/${result.displayName}`);
    } catch (error) {
      setStatus(`截图保存失败：${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4" data-testid="computer-use-panel">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold"><Laptop size={16} />{ui('本地电脑')}</div>
          <div className="mt-1 max-w-3xl text-xs leading-5 text-[#625a52]">
            {ui('截图 → 操作 → 自动复核的 Computer Use 循环；支持 Linux X11 与 Windows，并保留本机授权和随时接管。')}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-md border border-[#d7c8b4] bg-[#fffaf2] px-2 py-1 font-mono text-[10px] text-[#6b5541]">{protocol}</span>
          <span className={`flex items-center gap-1 rounded-md px-2 py-1 text-xs ${computerUseAvailable ? 'bg-[#dfead7] text-[#315d2e]' : 'bg-[#ead5d1] text-[#8a3b31]'}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${computerUseAvailable ? 'bg-[#4f8a47]' : 'bg-[#a85247]'}`} />
            {computerUseAvailable ? ui('Computer Use 已连接') : ui('不可用')}
          </span>
        </div>
      </div>

      <section className="mb-3 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-3" aria-label={ui('Computer Use 权限模式')}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-1 rounded-lg bg-[#eee7dd] p-1" role="group" aria-label={ui('电脑权限')}>
            {PERMISSION_MODES.map((item) => (
              <button
                key={item.mode}
                type="button"
                onClick={() => selectPermissionMode(item.mode)}
                aria-pressed={permissionMode === item.mode}
                title={ui(item.description)}
                className={`rounded-md px-3 py-1.5 text-xs transition ${permissionMode === item.mode ? 'bg-white font-semibold text-[#3b332c] shadow-sm' : 'text-[#625a52] hover:bg-white/60'}`}
              >
                {ui(item.label)}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={interruptComputer}
            disabled={!bridge || busy === 'interrupt'}
            className="flex items-center gap-1.5 rounded-md border border-[#b45b4e] bg-[#fff8f6] px-3 py-1.5 text-xs font-semibold text-[#8a3028] disabled:opacity-40"
          >
            <CircleStop size={14} />{busy === 'interrupt' ? ui('正在接管') : ui('立即接管')}
          </button>
        </div>
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-[#625a52]">
          <ShieldCheck size={13} />
          {permissionMode === 'manual' && ui('键鼠动作需要一次性勾选，并由系统原生对话框确认。')}
          {permissionMode === 'observe' && ui('模型和面板只能截图、放大与读取光标，不能注入键鼠事件。')}
          {permissionMode === 'auto' && ui('本次会话已授权；退出应用或点击“立即接管”会自动撤销。')}
        </div>
      </section>

      {status && (
        <div role="status" aria-live="polite" className="mb-3 break-all rounded-lg border border-[#d8cfc3] bg-[#fffdf9] px-3 py-2 text-xs leading-5 text-[#625a52]">
          {ui(status)}
        </div>
      )}

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.6fr)_minmax(17rem,0.8fr)]">
        <section className="overflow-hidden rounded-xl border border-[#332e29] bg-[#171614] text-[#f6efe5]" aria-label={ui('实时电脑画面')}>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 bg-[#211f1c] px-3 py-2">
            <div className="flex items-center gap-2 text-xs font-semibold">
              {visibleActiveAction ? <Radar className="aether-computer-radar text-[#e1a66c]" size={14} /> : <Eye size={14} />}
              {visibleActiveAction ? `${ui(ACTION_LABELS[visibleActiveAction])}…` : ui('实时桌面')}
            </div>
            <div className="flex items-center gap-2 font-mono text-[10px] text-[#bdb3a7]">
              {screenshot ? `${screenshot.width}×${screenshot.height} · ${screenshot.native_width}×${screenshot.native_height}` : ui('等待观察')}
              <span className={`h-1.5 w-1.5 rounded-full ${visibleActiveAction ? 'aether-computer-live-dot bg-[#e1a66c]' : 'bg-[#67a85e]'}`} />
            </div>
          </div>
          <div className="aether-computer-screen relative min-h-64 overflow-hidden bg-[#0d0d0c]">
            {screenshot ? (
              <>
                <img
                  ref={imageRef}
                  src={screenshot.image_data_url}
                  alt={ui('本机屏幕截图预览')}
                  onClick={handlePreviewClick}
                  className="block h-auto max-h-[34rem] w-full cursor-crosshair object-contain"
                />
                {coordinate && previewEffectStyle && (
                  <span
                    key={effect?.id || `${coordinate[0]}-${coordinate[1]}`}
                    className={`aether-computer-target ${effect && ['left_click', 'right_click', 'middle_click', 'double_click', 'triple_click'].includes(effect.action) ? 'aether-computer-click' : ''}`}
                    style={previewEffectStyle}
                    aria-hidden="true"
                  />
                )}
                {visibleActiveAction && <span className="aether-computer-scanline" aria-hidden="true" />}
                {zoomScreenshot && (
                  <div className="absolute bottom-3 right-3 w-[44%] overflow-hidden rounded-lg border border-[#e1a66c] bg-[#171614] shadow-2xl">
                    <div className="flex items-center justify-between border-b border-white/10 px-2 py-1 text-[10px] text-[#e8c69f]">
                      <span className="flex items-center gap-1"><ZoomIn size={11} />{ui('区域放大 · 原始像素')}</span>
                      <button onClick={() => setZoomScreenshot(null)} className="rounded px-1 text-white/70 hover:bg-white/10" aria-label={ui('关闭放大预览')}>×</button>
                    </div>
                    <img src={zoomScreenshot.image_data_url} alt={ui('放大区域预览')} className="block w-full" />
                  </div>
                )}
              </>
            ) : (
              <div className="flex min-h-64 flex-col items-center justify-center px-6 text-center text-[#bdb3a7]">
                <ScanLine size={30} className="mb-3 text-[#e1a66c]" />
                <div className="text-sm font-semibold text-[#f6efe5]">{ui('等待第一帧')}</div>
                <div className="mt-1 max-w-sm text-xs leading-5">{ui('观察后可直接在画面中点选坐标；只有执行动作时才会触发键鼠控制。')}</div>
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/10 bg-[#211f1c] px-3 py-2 text-[10px] text-[#bdb3a7]">
            <span className="flex items-center gap-1"><Crosshair size={11} />{coordinate ? `(${coordinate[0]}, ${coordinate[1]})` : ui('未选择坐标')}</span>
            <span>{ui('动作完成后自动截图复核')}</span>
          </div>
        </section>

        <section className="rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-3" aria-label={ui('动作回执')}>
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-sm font-semibold"><Sparkles size={15} />{ui('观察 · 操作 · 验证')}</div>
            <span className="rounded bg-[#eee7dd] px-2 py-1 text-[10px] text-[#625a52]">{timeline.length}/16</span>
          </div>
          <div className="mt-3 max-h-[34rem] space-y-2 overflow-auto pr-1" data-testid="computer-action-timeline">
            {timeline.length === 0 ? (
              <div className="rounded-lg border border-dashed border-[#d8cfc3] px-3 py-6 text-center text-xs leading-5 text-[#625a52]">
                {ui('动作会以实时步骤显示在这里，并附带耗时和截图验证结果。')}
              </div>
            ) : timeline.map((item, index) => (
              <div key={item.id} className="relative flex gap-2 rounded-lg border border-[#ded6ca] bg-white px-3 py-2">
                <div className="mt-0.5 flex flex-col items-center">
                  <span className={`flex h-5 w-5 items-center justify-center rounded-full ${
                    item.phase === 'verified' ? 'bg-[#dfead7] text-[#3f6c3a]'
                      : item.phase === 'running' ? 'aether-computer-step-running bg-[#f5dfc7] text-[#8d5f35]'
                        : 'bg-[#ead5d1] text-[#8a3b31]'
                  }`}>
                    {item.phase === 'verified' ? <Check size={12} /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
                  </span>
                  {index < timeline.length - 1 && <span className="mt-1 h-full min-h-3 w-px bg-[#ded6ca]" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="font-semibold">{ui(item.label)}</span>
                    <span className="font-mono text-[10px] text-[#625a52]">{item.durationMs !== undefined ? `${item.durationMs} ms` : '…'}</span>
                  </div>
                  <div className="mt-0.5 truncate text-[10px] text-[#625a52]">
                    {item.phase === 'running' ? ui('正在执行') : item.phase === 'verified' ? ui(item.detail || '截图已复核') : ui(item.detail || '已停止')}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="mt-3 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold"><Camera size={15} />{ui('Computer Use 控制台')}</div>
            <div className="mt-1 text-xs text-[#625a52]">{ui('点选画面或输入截图坐标；坐标会按截图缩放比例映射到原始桌面。')}</div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => runAction('screenshot')} disabled={!bridge || Boolean(visibleActiveAction)} className="rounded-md bg-[#2f2a24] px-3 py-1.5 text-xs text-white disabled:opacity-40">
              {visibleActiveAction === 'screenshot' ? ui('观察中') : ui('截取主屏幕')}
            </button>
            <button onClick={saveScreenshot} disabled={!screenshot || Boolean(busy)} className="rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-40">{ui('保存到“图片/Screenshots”')}</button>
          </div>
        </div>

        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <div className="rounded-lg border border-[#ded6ca] bg-white p-3">
            <div className="flex items-center gap-2 text-xs font-semibold"><MousePointer2 size={14} />{ui('指针与点击')}</div>
            <div className="mt-2 grid grid-cols-[1fr_1fr_auto] gap-2">
              <input value={mouseX} onChange={(event) => setMouseX(event.target.value)} inputMode="numeric" placeholder={ui('截图 X')} aria-label={ui('鼠标 X 坐标')} className="h-9 min-w-0 rounded-lg border border-[#ded6ca] px-3 text-sm outline-none" />
              <input value={mouseY} onChange={(event) => setMouseY(event.target.value)} inputMode="numeric" placeholder={ui('截图 Y')} aria-label={ui('鼠标 Y 坐标')} className="h-9 min-w-0 rounded-lg border border-[#ded6ca] px-3 text-sm outline-none" />
              <button onClick={() => runAction('cursor_position')} disabled={!bridge || Boolean(visibleActiveAction)} className="rounded-md border border-[#d6cbbd] px-3 text-xs disabled:opacity-40">{ui('读取位置')}</button>
            </div>
            <label className="mt-2 flex items-center gap-2 text-xs text-[#625a52]">
              <input type="checkbox" checked={actionArmed} disabled={!mouseAvailable || permissionMode !== 'manual'} onChange={(event) => setActionArmed(event.target.checked)} />
              {ui('我确认只授权下一次键盘或鼠标操作')}
            </label>
            <div className="mt-2 flex flex-wrap gap-2">
              <button onClick={() => runCoordinateAction('mouse_move')} disabled={!canControl || !screenshot || !mouseX || !mouseY || Boolean(visibleActiveAction)} className="rounded-md bg-[#8d5f35] px-3 py-1.5 text-xs text-white disabled:opacity-40">{ui('移动鼠标')}</button>
              <button onClick={() => runCoordinateAction('left_click')} disabled={!canControl || !screenshot || !mouseX || !mouseY || Boolean(visibleActiveAction)} className="rounded-md border border-[#8d5f35] px-3 py-1.5 text-xs text-[#6b4525] disabled:opacity-40">{ui('左键单击')}</button>
              <button onClick={() => runCoordinateAction('double_click')} disabled={!canControl || !screenshot || !mouseX || !mouseY || Boolean(visibleActiveAction)} className="rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-40">{ui('双击')}</button>
              <button onClick={() => runCoordinateAction('right_click')} disabled={!canControl || !screenshot || !mouseX || !mouseY || Boolean(visibleActiveAction)} className="rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-40">{ui('右键')}</button>
              <button onClick={zoomAtCoordinate} disabled={!screenshot || !mouseX || !mouseY || Boolean(visibleActiveAction)} className="flex items-center gap-1 rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-40"><ZoomIn size={12} />{ui('放大')}</button>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-[#eee7dd] pt-2">
              <button onClick={() => { try { setDragStart(actionCoordinate()); setStatus('已记录拖拽起点'); } catch (error) { setStatus(String(error)); } }} disabled={!mouseX || !mouseY} className="rounded-md border border-[#d6cbbd] px-2.5 py-1 text-[11px] disabled:opacity-40">{ui('设为拖拽起点')}</button>
              <button onClick={runDrag} disabled={!canControl || !screenshot || !dragStart || !mouseX || !mouseY || Boolean(visibleActiveAction)} className="rounded-md border border-[#d6cbbd] px-2.5 py-1 text-[11px] disabled:opacity-40">{ui('拖拽到当前坐标')}</button>
              {dragStart && <span className="text-[10px] text-[#625a52]">{ui('起点')} ({dragStart[0]}, {dragStart[1]})</span>}
            </div>
          </div>

          <div className="rounded-lg border border-[#ded6ca] bg-white p-3">
            <div className="flex items-center gap-2 text-xs font-semibold"><Keyboard size={14} />{ui('键盘与滚动')}</div>
            <textarea value={textInput} onChange={(event) => setTextInput(event.target.value)} placeholder={ui('输入要键入到当前焦点的文本')} aria-label={ui('Computer Use 输入文本')} className="mt-2 h-20 w-full resize-y rounded-lg border border-[#ded6ca] p-2 text-xs outline-none" />
            <div className="mt-2 flex flex-wrap gap-2">
              <button onClick={() => runAction('type', { text: textInput })} disabled={!canControl || !textInput || Boolean(visibleActiveAction)} className="rounded-md bg-[#8d5f35] px-3 py-1.5 text-xs text-white disabled:opacity-40">{ui('输入文本')}</button>
              <input value={keyInput} onChange={(event) => setKeyInput(event.target.value)} aria-label={ui('按键组合')} className="h-8 min-w-0 flex-1 rounded-md border border-[#ded6ca] px-2 font-mono text-xs outline-none" />
              <button onClick={() => runAction('key', { text: keyInput })} disabled={!canControl || !keyInput || Boolean(visibleActiveAction)} className="rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-40">{ui('发送按键')}</button>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-[#eee7dd] pt-2">
              <input value={scrollAmount} onChange={(event) => setScrollAmount(event.target.value)} inputMode="numeric" aria-label={ui('滚动量')} className="h-8 w-20 rounded-md border border-[#ded6ca] px-2 text-xs outline-none" />
              <button onClick={() => runAction('scroll', { coordinate: coordinate || undefined, scroll_direction: 'up', scroll_amount: Number(scrollAmount) || 600 })} disabled={!canControl || Boolean(visibleActiveAction)} className="rounded-md border border-[#d6cbbd] px-2.5 py-1 text-[11px] disabled:opacity-40">{ui('向上滚动')}</button>
              <button onClick={() => runAction('scroll', { coordinate: coordinate || undefined, scroll_direction: 'down', scroll_amount: Number(scrollAmount) || 600 })} disabled={!canControl || Boolean(visibleActiveAction)} className="rounded-md border border-[#d6cbbd] px-2.5 py-1 text-[11px] disabled:opacity-40">{ui('向下滚动')}</button>
              <button onClick={() => runAction('wait', { duration: 1 })} disabled={!bridge || Boolean(visibleActiveAction)} className="rounded-md border border-[#d6cbbd] px-2.5 py-1 text-[11px] disabled:opacity-40">{ui('等待 1 秒')}</button>
            </div>
          </div>
        </div>
      </section>

      <DesktopDocumentPanel onStatus={setStatus} />
    </div>
  );
}
