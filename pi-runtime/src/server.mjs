import { randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { pathToFileURL } from 'node:url';
import { createCompletion } from './completion.mjs';

const maxInFlight = 4;
const maxBodyBytes = 2 * 1024 * 1024;
const maxTimerMs = 2 ** 31 - 1;
const defaultKeepaliveMs = 15000;
const minMaxStreamDurationMs = 30 * 60 * 1000;
const requestFields = new Set([
  'model', 'messages', 'tools', 'toolChoice', 'temperature', 'maxTokens',
  'timeoutMs', 'maxRetries', 'parameters', 'stream',
]);

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function inRange(value, min, max) {
  return Number.isSafeInteger(value) && value >= min && value <= max;
}

function validParameters(parameters) {
  return isRecord(parameters) && Object.entries(parameters).every(([key, value]) => {
    if (key === 'prompt_cache_key') return value === 'gold-analysis';
    if (key === 'reasoning_effort') return value === 'max';
    if (key === 'thinking') {
      return isRecord(value) && Object.keys(value).length === 1 && ['enabled', 'disabled'].includes(value.type);
    }
    return false;
  });
}

function validTools(tools, choice) {
  if (tools === undefined) return choice === undefined;
  if (!Array.isArray(tools) || !tools.every(tool => tool?.type === 'function'
    && typeof tool.function?.name === 'string' && typeof tool.function.description === 'string'
    && isRecord(tool.function.parameters))) return false;
  return choice === undefined || ['auto', 'none', 'required'].includes(choice)
    || (choice?.type === 'function' && tools.some(tool => tool.function.name === choice.function?.name));
}

function validRequest(body, models, maxTimeoutMs, maxRetries) {
  return isRecord(body) && Object.keys(body).every(key => requestFields.has(key)) && models.has(body.model)
    && Array.isArray(body.messages) && body.messages.length > 0
    && body.messages.every((message, index) => typeof message?.content === 'string'
      && (message.role === 'user' || (index === 0 && message.role === 'system')))
    && body.messages.some(message => message.role === 'user')
    && Number.isFinite(body.temperature) && body.temperature >= 0 && body.temperature <= 2
    && inRange(body.maxTokens, 1, 32768) && inRange(body.timeoutMs, 1, maxTimeoutMs)
    && inRange(body.maxRetries, 0, maxRetries)
    && (body.stream === undefined || typeof body.stream === 'boolean')
    && validParameters(body.parameters) && validTools(body.tools, body.toolChoice);
}

async function readBody(request) {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of request.iterator({ destroyOnReturn: false })) {
    bytes += chunk.length;
    if (bytes > maxBodyBytes) return undefined;
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function send(response, status, value) {
  if (response.destroyed) return;
  response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
  response.end(JSON.stringify(value));
}

export function createPiServer({
  baseUrl,
  apiKey,
  modelIds,
  maxTimeoutMs = 120000,
  maxRetries = 3,
  keepaliveMs = defaultKeepaliveMs,
  maxStreamDurationMs = Math.min(maxTimerMs, Math.max(maxTimeoutMs * 6, minMaxStreamDurationMs)),
  fetch,
}) {
  const url = URL.parse(baseUrl);
  if (!url || !['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('LLM_BASE_URL must be an HTTP(S) URL without credentials, query or fragment');
  }
  if (!apiKey?.trim()) throw new Error('LLM_API_KEY is required');
  if (
    !inRange(maxTimeoutMs, 1, maxTimerMs)
    || !inRange(maxRetries, 0, Number.MAX_SAFE_INTEGER)
    || !inRange(keepaliveMs, 1, maxTimerMs)
    || !inRange(maxStreamDurationMs, 1, maxTimerMs)
  ) {
    throw new Error('Invalid LLM_TIMEOUT, LLM_MAX_RETRIES, keepaliveMs or maxStreamDurationMs');
  }
  const models = new Set(modelIds.map(model => model.trim()).filter(Boolean));
  if (!models.size) throw new Error('At least one LLM model is required');
  const complete = createCompletion({ baseUrl: url.href.replace(/\/$/, ''), apiKey, modelIds: models, fetch });

  let inFlight = 0;
  return createServer({ headersTimeout: 10000, requestTimeout: 15000 }, async (request, response) => {
    if (request.method === 'GET' && request.url === '/healthz') return send(response, 200, { status: 'ok' });
    if (request.method !== 'POST' || request.url !== '/v1/complete') {
      request.resume();
      return send(response, 404, { error: 'not_found' });
    }
    response.setHeader('x-request-id', randomUUID());
    if (inFlight >= maxInFlight) {
      request.resume();
      return send(response, 503, { error: 'busy' });
    }

    inFlight += 1;
    const disconnect = new AbortController();
    response.once('close', () => {
      if (!response.writableFinished) disconnect.abort();
    });
    let clearTimers = () => {};
    let sseStarted = false;
    let timedOut = false;
    const writeSse = (event, data) => {
      if (!sseStarted || disconnect.signal.aborted || response.destroyed || response.writableEnded) {
        return;
      }
      response.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    };
    const finishFailure = (status, payload) => {
      if (sseStarted) {
        writeSse('error', payload);
        if (!response.destroyed && !response.writableEnded) response.end();
        return;
      }
      return send(response, status, payload);
    };
    try {
      let body;
      try {
        body = await readBody(request);
      } catch {
        return send(response, 400, { error: 'invalid_json' });
      }
      if (body === undefined) {
        request.resume();
        response.setHeader('connection', 'close');
        return send(response, 413, { error: 'body_too_large' });
      }
      if (!validRequest(body, models, maxTimeoutMs, maxRetries)) {
        return send(response, 400, { error: 'invalid_completion_request' });
      }
      if (disconnect.signal.aborted) return;

      if (body.stream === false) {
        const deadline = AbortSignal.timeout(body.timeoutMs);
        timedOut = false;
        const result = await complete(body, AbortSignal.any([disconnect.signal, deadline]));
        if (disconnect.signal.aborted) return;
        if (deadline.aborted) return send(response, 504, { error: 'deadline_exceeded' });
        if (result.stopReason === 'stop' || result.stopReason === 'toolUse') return send(response, 200, result);
        return send(response, 502, { error: 'upstream_completion_failed', stopReason: result.stopReason });
      }

      const idleController = new AbortController();
      let idleTimer;
      let maxTotalTimer;
      let keepaliveTimer;

      clearTimers = () => {
        clearTimeout(idleTimer);
        clearTimeout(maxTotalTimer);
        clearInterval(keepaliveTimer);
      };
      disconnect.signal.addEventListener('abort', clearTimers, { once: true });

      const resetIdleTimer = () => {
        if (disconnect.signal.aborted || idleController.signal.aborted) return;
        clearTimeout(idleTimer);
        idleTimer = setTimeout(() => {
          timedOut = true;
          idleController.abort();
        }, body.timeoutMs);
        idleTimer.unref?.();
      };

      const startSse = () => {
        if (sseStarted || disconnect.signal.aborted || response.destroyed || response.writableEnded) {
          return;
        }
        sseStarted = true;
        request.socket?.setTimeout?.(0);
        response.setTimeout?.(0);
        response.writeHead(200, {
          'content-type': 'text/event-stream; charset=utf-8',
          'cache-control': 'no-store',
          connection: 'keep-alive',
          'x-accel-buffering': 'no',
        });
        response.flushHeaders?.();
        keepaliveTimer = setInterval(() => {
          writeSse('ping', {});
        }, keepaliveMs);
        keepaliveTimer.unref?.();
      };

      const totalBudgetMs = Math.max(body.timeoutMs, maxStreamDurationMs);
      maxTotalTimer = setTimeout(() => {
        timedOut = true;
        idleController.abort();
      }, totalBudgetMs);
      maxTotalTimer.unref?.();
      resetIdleTimer();

      const result = await complete(body, AbortSignal.any([disconnect.signal, idleController.signal]), {
        onActivity: resetIdleTimer,
        onStreamStart: startSse,
        onEvent: event => {
          startSse();
          if (event.type === 'thinking') writeSse('thinking', { delta: event.delta });
          else if (event.type === 'text') writeSse('text', { delta: event.delta });
          else if (event.type === 'tool_call') writeSse('tool_call', { active: true });
        },
      });
      clearTimers();

      if (disconnect.signal.aborted) return;
      if (timedOut) {
        return finishFailure(504, { error: 'deadline_exceeded' });
      }
      if (result.stopReason === 'stop' || result.stopReason === 'toolUse') {
        startSse();
        writeSse('done', result);
        response.end();
        return;
      }
      return finishFailure(502, { error: 'upstream_completion_failed', stopReason: result.stopReason });
    } catch {
      clearTimers();
      if (disconnect.signal.aborted) return;
      if (timedOut) {
        return finishFailure(504, { error: 'deadline_exceeded' });
      }
      return finishFailure(502, { error: 'upstream_completion_failed' });
    } finally {
      clearTimers();
      inFlight -= 1;
    }
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const env = process.env;
  const server = createPiServer({
    baseUrl: env.LLM_BASE_URL || 'https://api.openai.com/v1',
    apiKey: env.LLM_API_KEY,
    modelIds: [env.LLM_MODEL || 'gpt-4o', env.LLM_TRADE_MODEL || 'deepseek-v4-flash-0731',
      env.LLM_FALLBACK_MODEL || 'gpt-4o-mini'],
    maxTimeoutMs: env.LLM_TIMEOUT ? Number(env.LLM_TIMEOUT) : 120000,
    maxRetries: env.LLM_MAX_RETRIES !== undefined && env.LLM_MAX_RETRIES !== '' ? Number(env.LLM_MAX_RETRIES) : 3,
    ...(env.PI_MAX_STREAM_DURATION_MS !== undefined && env.PI_MAX_STREAM_DURATION_MS !== ''
      ? { maxStreamDurationMs: Number(env.PI_MAX_STREAM_DURATION_MS) }
      : {}),
  });
  server.listen(3000, env.PI_RUNTIME_HOST || '127.0.0.1');
}
