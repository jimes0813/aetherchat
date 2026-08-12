import { useMemo, useState, type ReactNode } from 'react';

export type StartupRuntime = {
  backendStatus?: 'starting' | 'ready' | 'unavailable';
  backendErrorCode?: string;
  backendRequestId?: string;
};

type StartupGateProps = {
  runtime?: StartupRuntime;
  children: ReactNode;
  onRetry?: () => void;
};

const RECOVERY: Record<string, { title: string; detail: string }> = {
  database_schema_mismatch: {
    title: '本机数据库版本不兼容',
    detail: '请不要删除用户数据。先保留数据库备份，再安装匹配版本或执行受支持的迁移流程。',
  },
  database_unavailable: {
    title: '本机数据库暂不可用',
    detail: '请检查用户数据目录权限、磁盘剩余空间，并在排除占用后重试。',
  },
  backend_start_timeout: {
    title: '本机服务启动超时',
    detail: '服务没有在限定时间内完成就绪检查。可重试；若仍失败，请携带诊断 ID 查看后端日志。',
  },
  backend_executable_missing: {
    title: '安装内容不完整',
    detail: '本机服务程序缺失。请使用官方安装包覆盖安装，用户数据不会因此被删除。',
  },
  backend_port_unavailable: {
    title: '本机端口不可用',
    detail: 'AetherChat 无法找到可用的本机端口。请退出冲突进程或重启系统后再试。',
  },
  backend_process_failed: {
    title: '本机服务进程启动失败',
    detail: '请重试启动；若问题持续，请携带诊断 ID 与错误码检查安装日志。',
  },
  backend_start_failed: {
    title: '本机服务准备失败',
    detail: '请重试启动；若问题持续，请携带诊断 ID 与错误码检查安装日志。',
  },
};

function localDiagnosticId() {
  const value = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `desktop-${value}`;
}

export function StartupGate({ runtime, children, onRetry }: StartupGateProps) {
  const [copied, setCopied] = useState(false);
  const generatedDiagnosticId = useMemo(localDiagnosticId, []);
  const status = runtime?.backendStatus;

  if (!status || status === 'ready') return children;

  if (status === 'starting') {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[#f5efe7] p-6 text-[#2f2925]">
        <section role="status" className="w-full max-w-lg rounded-2xl border border-[#d8cec4] bg-white p-7 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#7a695d]">AetherChat Desktop</p>
          <h1 className="mt-3 text-xl font-semibold">正在检查本机服务</h1>
          <p className="mt-3 text-sm leading-6 text-[#665b53]">正在确认数据库与核心服务已就绪，请稍候。</p>
        </section>
      </main>
    );
  }

  const code = runtime?.backendErrorCode || 'backend_not_ready';
  const recovery = RECOVERY[code] || {
    title: 'AetherChat 尚未就绪',
    detail: '本机核心服务当前不可用。请重试启动；若仍失败，请携带下方诊断信息联系维护者。',
  };
  const diagnosticId = runtime?.backendRequestId || generatedDiagnosticId;
  const copyDiagnosticId = async () => {
    try {
      await navigator.clipboard.writeText(diagnosticId);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  const retry = onRetry || (() => window.location.reload());

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f5efe7] p-6 text-[#2f2925]">
      <section role="alert" className="w-full max-w-xl rounded-2xl border border-[#d9b6af] bg-[#fffaf7] p-7 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#8a4f43]">AetherChat Desktop</p>
        <h1 className="mt-3 text-xl font-semibold">{recovery.title}</h1>
        <p className="mt-3 text-sm leading-6 text-[#66534e]">{recovery.detail}</p>
        <dl className="mt-5 grid gap-2 rounded-xl bg-white/80 p-4 text-xs">
          <div className="flex flex-wrap justify-between gap-2">
            <dt className="font-medium text-[#76645d]">错误码</dt>
            <dd><code>{code}</code></dd>
          </div>
          <div className="flex flex-wrap justify-between gap-2">
            <dt className="font-medium text-[#76645d]">诊断 ID</dt>
            <dd><code>{diagnosticId}</code></dd>
          </div>
        </dl>
        <div className="mt-5 flex flex-wrap gap-2">
          <button type="button" onClick={() => void copyDiagnosticId()} className="rounded-lg border border-[#8a4f43] px-3 py-2 text-sm text-[#713d34]">
            {copied ? '诊断 ID 已复制' : '复制诊断 ID'}
          </button>
          <button type="button" onClick={retry} className="rounded-lg bg-[#713d34] px-3 py-2 text-sm text-white">
            重试启动
          </button>
        </div>
        <p className="mt-4 text-xs leading-5 text-[#806e66]">重试不会清除聊天记录、工作区或模型配置。</p>
      </section>
    </main>
  );
}
