'use strict';

const http = require('http');
const { timingSafeEqual } = require('crypto');

/**
 * Model Computer Use consent broker (loopback HTTP + native dialog).
 * Extracted from main.cjs to shrink the Electron entrypoint.
 */
function createComputerConsentBroker({
  dialog,
  BrowserWindow,
  token,
}) {
  let server = null;
  let url = '';
  let sessionApproved = false;
  let queue = Promise.resolve();

  function secureTokenMatches(candidate) {
    const actual = Buffer.from(String(candidate || ''), 'utf8');
    const expected = Buffer.from(token, 'utf8');
    return actual.length === expected.length && timingSafeEqual(actual, expected);
  }

  async function confirmModelComputerUse(payload) {
    const risk = String(payload?.risk || 'computer_control');
    const alwaysConfirm = new Set(['file_write', 'app_launch']).has(risk);
    if (sessionApproved && !alwaysConfirm) return { approved: true, scope: 'session' };

    const owner = BrowserWindow.getAllWindows().find((window) => !window.isDestroyed());
    const action = String(payload?.action || '电脑操作').slice(0, 80);
    const detail = String(payload?.detail || '').slice(0, 2000);
    const buttons = alwaysConfirm
      ? ['允许本次操作', '取消']
      : ['允许本次操作', '允许本次会话', '拒绝'];
    const cancelId = buttons.length - 1;
    const result = await dialog.showMessageBox(owner || undefined, {
      type: 'warning',
      title: 'AetherChat 模型电脑控制',
      message: `模型请求执行：${action}`,
      detail: `${detail}\n\n“允许本次会话”会授权模型观察屏幕并操作键鼠，直到退出 AetherChat。所有操作都会记录在工具运行日志中。`,
      buttons,
      defaultId: cancelId,
      cancelId,
      noLink: true,
    });
    if (!alwaysConfirm && result.response === 1) {
      sessionApproved = true;
      return { approved: true, scope: 'session' };
    }
    return { approved: result.response === 0, scope: result.response === 0 ? 'once' : 'denied' };
  }

  function start() {
    if (server && url) return Promise.resolve(url);
    return new Promise((resolve, reject) => {
      const next = http.createServer((request, response) => {
        const send = (statusCode, payload) => {
          const body = Buffer.from(JSON.stringify(payload), 'utf8');
          response.writeHead(statusCode, {
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': body.length,
            'Cache-Control': 'no-store',
          });
          response.end(body);
        };
        if (request.method !== 'POST' || request.url !== '/consent') {
          send(404, { approved: false, error: 'not_found' });
          return;
        }
        if (!secureTokenMatches(request.headers['x-aether-consent-token'])) {
          send(403, { approved: false, error: 'forbidden' });
          return;
        }
        const chunks = [];
        let size = 0;
        request.on('data', (chunk) => {
          size += chunk.length;
          if (size > 64 * 1024) {
            request.destroy();
            return;
          }
          chunks.push(chunk);
        });
        request.on('end', async () => {
          if (size > 64 * 1024) return;
          let payload;
          try {
            payload = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
          } catch {
            send(400, { approved: false, error: 'invalid_json' });
            return;
          }
          queue = queue.catch(() => undefined).then(() => confirmModelComputerUse(payload));
          try {
            send(200, await queue);
          } catch (error) {
            send(500, { approved: false, error: String(error?.message || error) });
          }
        });
      });
      next.once('error', reject);
      next.listen(0, '127.0.0.1', () => {
        const address = next.address();
        if (!address || typeof address === 'string') {
          next.close();
          reject(new Error('Unable to bind the model consent broker'));
          return;
        }
        server = next;
        url = `http://127.0.0.1:${address.port}/consent`;
        resolve(url);
      });
    });
  }

  function stop() {
    sessionApproved = false;
    url = '';
    if (server) server.close();
    server = null;
  }

  function getUrl() {
    return url;
  }

  function resetSessionApproval() {
    sessionApproved = false;
  }

  return {
    start,
    stop,
    getUrl,
    resetSessionApproval,
    confirmModelComputerUse,
    secureTokenMatches,
  };
}

module.exports = {
  createComputerConsentBroker,
};
