import React, { Component, type ErrorInfo, type ReactNode } from 'react';
import { useI18n } from '../i18n';

type ErrorWithRequestId = Error & { requestId?: string };

interface ErrorBoundaryProps {
  children: ReactNode;
  scope: string;
  resetKey?: string;
  fallbackClassName?: string;
}

type ErrorBoundaryCoreProps = ErrorBoundaryProps & {
  ui: (text: string, values?: Record<string, string | number>) => string;
};

interface ErrorBoundaryState {
  error: ErrorWithRequestId | null;
  diagnosticId: string | null;
  copied: boolean;
}

function createDiagnosticId() {
  const id = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `ui-${id}`;
}

class ErrorBoundaryCore extends Component<ErrorBoundaryCoreProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = {
    error: null,
    diagnosticId: null,
    copied: false,
  };

  static getDerivedStateFromError(error: ErrorWithRequestId): Partial<ErrorBoundaryState> {
    return {
      error,
      diagnosticId: error.requestId || createDiagnosticId(),
      copied: false,
    };
  }

  componentDidCatch(error: ErrorWithRequestId, info: ErrorInfo) {
    console.error('AetherChat UI boundary', {
      scope: this.props.scope,
      diagnosticId: this.state.diagnosticId,
      error: error.message,
      componentStack: info.componentStack,
    });
  }

  componentDidUpdate(previous: ErrorBoundaryCoreProps) {
    if (this.state.error && previous.resetKey !== this.props.resetKey) {
      this.reset();
    }
  }

  private reset = () => {
    this.setState({ error: null, diagnosticId: null, copied: false });
  };

  private copyDiagnosticId = async () => {
    if (!this.state.diagnosticId) return;
    try {
      await navigator.clipboard.writeText(this.state.diagnosticId);
      this.setState({ copied: true });
    } catch {
      this.setState({ copied: false });
    }
  };

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <section
        role="alert"
        className={this.props.fallbackClassName || 'm-4 rounded-lg border border-[#d9b6af] bg-[#f8ece9] p-5 text-[#67372f]'}
      >
        <h2 className="text-sm font-semibold">
          {this.props.ui('{scope}暂时不可用', { scope: this.props.ui(this.props.scope) })}
        </h2>
        <p className="mt-2 text-xs leading-5">
          {this.props.ui('已隔离这个界面的异常，其他数据不会因此被清除。可先重试；若仍失败，请把诊断码提供给维护者。')}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <code className="rounded bg-white/70 px-2 py-1">{this.state.diagnosticId}</code>
          <button type="button" onClick={() => void this.copyDiagnosticId()} className="rounded border border-current px-2 py-1">
            {this.state.copied ? this.props.ui('已复制') : this.props.ui('复制诊断码')}
          </button>
          <button type="button" onClick={this.reset} className="rounded border border-current px-2 py-1">{this.props.ui('重试此区域')}</button>
          <button type="button" onClick={() => window.location.reload()} className="rounded bg-[#67372f] px-2 py-1 text-white">{this.props.ui('重新载入应用')}</button>
        </div>
      </section>
    );
  }
}

export function ErrorBoundary(props: ErrorBoundaryProps) {
  const { ui } = useI18n();
  return <ErrorBoundaryCore {...props} ui={ui} />;
}
