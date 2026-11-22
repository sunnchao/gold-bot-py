import assert from 'node:assert/strict';
import { once } from 'node:events';
import { test } from 'node:test';
import { createPiServer } from '../src/server.mjs';

const primary = 'kimi-test';
const secondary = 'deepseek-test';
const schema = { type: 'object', properties: { confidence: { type: 'integer' } }, required: ['confidence'] };
const requestBody = {
  model: primary,
  messages: [
    { role: 'system', content: 'stable\n\nsymbol' },
    { role: 'user', content: 'context' },
    { role: 'user', content: 'live' },
  ],
  tools: [{ type: 'function', function: { name: 'submit_analysis', description: '', parameters: schema } }],
  toolChoice: { type: 'function', function: { name: 'submit_analysis' } },
  maxTokens: 32768,
  temperature: 0.1,
  timeoutMs: 3000,
  maxRetries: 0,
  parameters: { prompt_cache_key: 'gold-analysis', reasoning_effort: 'max', thinking: { type: 'enabled' } },
};

function event(delta, finishReason = null) {
  return { choices: [{ index: 0, delta, finish_reason: finishReason }] };
}

function call(index, id, args) {
  return { index, id, type: 'function', function: { name: 'submit_analysis', arguments: args } };
}

function sse(frames) {
  const body = frames.map(frame => `data: ${JSON.stringify({ id: 'fake-completion', ...frame })}\n\n`).join('');
  return new Response(body + 'data: [DONE]\n\n', { headers: { 'Content-Type': 'text/event-stream' } });
}

async function readSse(response) {
  assert.match(response.headers.get('content-type') ?? '', /^text\/event-stream\b/);
  const raw = await response.text();
  const events = [];
  for (const block of raw.split(/\r?\n\r?\n/)) {
    if (!block.trim()) continue;
    let eventName = 'message';
    const dataLines = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith(':')) continue;
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
    }
    if (dataLines.length > 0) {
      events.push({ event: eventName, data: JSON.parse(dataLines.join('\n')) });
    }
  }
  const doneEvent = events.findLast(entry => entry.event === 'done');
  return { events, done: doneEvent?.data ?? null };
}

async function startServer(context, upstreamFetch, options = {}) {
  const server = createPiServer({
    baseUrl: 'https://gateway.invalid/v1', apiKey: 'offline-key', modelIds: [primary, secondary],
    maxTimeoutMs: options.maxTimeoutMs ?? 3000,
    maxRetries: options.maxRetries ?? 1,
    keepaliveMs: options.keepaliveMs,
    maxStreamDurationMs: options.maxStreamDurationMs,
    fetch: upstreamFetch,
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  context.after(() => {
    server.closeAllConnections();
    return new Promise(resolve => server.close(resolve));
  });
  const baseUrl = `http://127.0.0.1:${server.address().port}`;
  const complete = (body = requestBody, signal = AbortSignal.timeout(6000)) => fetch(`${baseUrl}/v1/complete`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  });
  return { baseUrl, complete };
}

test('preserves the wire contract, streams thinking, and picks the lowest valid tool index', async context => {
  const requests = [];
  const { complete } = await startServer(context, async (resource, init) => {
    const request = new Request(resource, init);
    assert.equal(request.url, 'https://gateway.invalid/v1/chat/completions');
    assert.equal(request.headers.get('authorization'), 'Bearer offline-key');
    requests.push(await request.json());
    return sse([
      event({ reasoning_content: 'private thinking' }),
      event({ content: 'answer', tool_calls: [call(3, 'third', '{"confidence":40}')] }),
      event({ tool_calls: [call(0, 'invalid', '{"confidence":')] }),
      event({ tool_calls: [call(1, 'first-valid', '{"confidence":')] }),
      event({ tool_calls: [{ index: 1, function: { arguments: '80}' } }] }, 'tool_calls'),
      { choices: [], usage: { prompt_tokens: 100, completion_tokens: 20, prompt_tokens_details: {
        cached_tokens: 30, cache_write_tokens: 5,
      } } },
    ]);
  });

  const response = await complete();
  assert.equal(response.status, 200);
  assert.ok(response.headers.get('x-request-id'));
  const { events, done } = await readSse(response);
  assert.deepEqual(events.filter(entry => entry.event === 'thinking').map(entry => entry.data.delta), ['private thinking']);
  assert.deepEqual(events.filter(entry => entry.event === 'text').map(entry => entry.data.delta), ['answer']);
  assert.ok(events.some(entry => entry.event === 'tool_call' && entry.data.active === true));
  assert.deepEqual(done, {
    content: 'answer', thinking: 'private thinking', stopReason: 'toolUse',
    toolUse: { id: 'first-valid', name: 'submit_analysis', input: { confidence: 80 } },
    cacheStats: { inputTokens: 100, readTokens: 30, hitTokens: 30, creationTokens: 5, missTokens: 0 },
  });
  assert.deepEqual(requests, [{
    model: primary, messages: requestBody.messages, tools: requestBody.tools, tool_choice: requestBody.toolChoice,
    prompt_cache_key: 'gold-analysis', stream: true, stream_options: { include_usage: true },
    max_completion_tokens: 32768, temperature: 0.1, reasoning_effort: 'max', thinking: { type: 'enabled' },
  }]);
});

test('normalizes native and nested usage without treating missing usage as zero', async context => {
  const fixtures = [
    [{ prompt_tokens: 100, prompt_cache_hit_tokens: 30, prompt_cache_miss_tokens: 70 }, 30, 70],
    [{ prompt_tokens: 100, cached_tokens: 27 }, 27, 0],
    [{ prompt_tokens: 100, billing_usage: { openai_usage: { prompt_tokens_details: { cached_tokens: 23 } } } }, 23, 0],
    [{ billing_usage: { openai_usage: { prompt_tokens: 100, prompt_tokens_details: { cached_tokens: 11 } } } }, 11, 0],
    [undefined, null, null],
  ];
  let index = 0;
  const { complete } = await startServer(context, async () => {
    const [usage] = fixtures[index++];
    return sse([event({}, 'stop'), { choices: [], usage }]);
  });

  for (const [, hitTokens, missTokens] of fixtures) {
    const response = await complete();
    assert.equal(response.status, 200);
    const { done: completion } = await readSse(response);
    assert.equal(completion.content, '');
    assert.equal(completion.thinking, '');
    assert.equal(completion.toolUse, null);
    assert.deepEqual(completion.cacheStats, hitTokens === null ? null : {
      inputTokens: 100, readTokens: hitTokens, hitTokens, creationTokens: 0, missTokens,
    });
  }
});

test('rejects unknown models and parameters before reaching the gateway', async context => {
  const { baseUrl, complete } = await startServer(context, () => assert.fail('must not call gateway'));
  assert.equal((await fetch(`${baseUrl}/healthz`)).status, 200);
  for (const body of [
    { ...requestBody, model: 'unconfigured' },
    { ...requestBody, baseUrl: 'https://other.invalid/v1' },
    { ...requestBody, parameters: { stream: false } },
    { ...requestBody, timeoutMs: 3001 },
  ]) {
    assert.equal((await complete(body)).status, 400);
  }
});

test('does not return partial or truncated tool data after stream failure', async context => {
  const stops = [null, 'length'];
  const { complete } = await startServer(context, async () => sse([
    event({ content: 'partial response', tool_calls: [call(0, 'partial', '{"confidence":80}')] }, stops.shift()),
  ]));

  for (const _ of [null, 'length']) {
    const response = await complete();
    assert.equal(response.status, 200);
    const { events, done } = await readSse(response);
    assert.equal(done, null);
    const errEvent = events.find(entry => entry.event === 'error');
    assert.equal(errEvent?.data?.error, 'upstream_completion_failed');
    assert.doesNotMatch(JSON.stringify(events), /confidence/);
  }
});

test('retries a rate limit only as configured and never switches models', async context => {
  const models = [];
  const { complete } = await startServer(context, async (resource, init) => {
    const body = await new Request(resource, init).json();
    models.push(body.model);
    if (models.length < 3) {
      return Response.json({ error: { message: 'gateway-private-error' } }, {
        status: 429, headers: { 'Retry-After': '0.01' },
      });
    }
    assert.equal(body.tool_choice, 'required');
    assert.equal(body.thinking.type, 'disabled');
    assert.equal(body.prompt_cache_key, undefined);
    return sse([event({ content: 'ok' }, 'stop')]);
  });

  const failed = await complete();
  assert.equal(failed.status, 502);
  assert.doesNotMatch(await failed.text(), /gateway-private-error/);
  const success = await complete({ ...requestBody, model: secondary, toolChoice: 'required', maxRetries: 1,
    parameters: { reasoning_effort: 'max', thinking: { type: 'disabled' } } });
  assert.equal(success.status, 200);
  assert.deepEqual(models, [primary, secondary, secondary]);
});

test('ends the upstream request on either deadline or client disconnect', async context => {
  const upstreamStarted = Promise.withResolvers();
  const disconnectObserved = Promise.withResolvers();
  let started = 0;
  const { complete } = await startServer(context, async (resource, init) => {
    const signal = new Request(resource, init).signal;
    started += 1;
    return new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => {
        if (started === 2) disconnectObserved.resolve();
        reject(signal.reason);
      }, { once: true });
      if (started === 2) upstreamStarted.resolve();
    });
  });
  const deadline = await complete({ ...requestBody, timeoutMs: 120 });
  assert.equal(deadline.status, 504);

  const controller = new AbortController();
  const responsePromise = complete(requestBody, controller.signal);
  await upstreamStarted.promise;
  controller.abort();
  await assert.rejects(responsePromise, { name: 'AbortError' });
  await Promise.race([disconnectObserved.promise, once(AbortSignal.timeout(2000), 'abort').then(() => {
    assert.fail('upstream did not observe client disconnect');
  })]);
  assert.equal(started, 2);
});

test('enforces request validation, body limits, route matching, and upstream 5xx masking', async context => {
  const { baseUrl, complete } = await startServer(context, async () => Response.json(
    { error: { message: 'secret-gateway-detail' } },
    { status: 500 },
  ));

  const notFound = await fetch(`${baseUrl}/unknown`, { method: 'POST' });
  assert.equal(notFound.status, 404);

  const badJson = await fetch(`${baseUrl}/v1/complete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{not-json',
  });
  assert.equal(badJson.status, 400);

  const systemOnly = await complete({
    ...requestBody,
    messages: [{ role: 'system', content: 'only system' }],
  });
  assert.equal(systemOnly.status, 400);

  const oversized = await complete({
    ...requestBody,
    messages: [{ role: 'user', content: 'x'.repeat(2 * 1024 * 1024 + 64) }],
  });
  assert.equal(oversized.status, 413);

  const upstream500 = await complete();
  assert.equal(upstream500.status, 502);
  assert.doesNotMatch(await upstream500.text(), /secret-gateway-detail/);
});

test('isolates concurrent completions and returns 503 when capacity is saturated', async context => {
  const releaseAll = Promise.withResolvers();
  let seen = 0;
  const allFourStarted = Promise.withResolvers();
  const { complete } = await startServer(context, async (resource, init) => {
    const body = await new Request(resource, init).json();
    const tag = body.messages[0].content;
    seen += 1;
    if (seen === 4) allFourStarted.resolve();
    await releaseAll.promise;
    return sse([event({ content: `reply:${tag}` }, 'stop')]);
  });

  const inFlight = [1, 2, 3, 4].map(i => complete({
    ...requestBody,
    messages: [{ role: 'user', content: `req-${i}` }],
  }));
  await allFourStarted.promise;

  const fifth = await complete({
    ...requestBody,
    messages: [{ role: 'user', content: 'req-5' }],
  });
  assert.equal(fifth.status, 503);
  assert.deepEqual(await fifth.json(), { error: 'busy' });

  releaseAll.resolve();
  const responses = await Promise.all(inFlight);
  const contents = await Promise.all(responses.map(async response => {
    assert.equal(response.status, 200);
    return (await readSse(response)).done.content;
  }));
  assert.deepEqual(contents, ['reply:req-1', 'reply:req-2', 'reply:req-3', 'reply:req-4']);
});

test('selects lowest index when tool_call id is omitted and skips non-object arguments', async context => {
  const { complete } = await startServer(context, async () => sse([
    event({ tool_calls: [{ index: 0, type: 'function', function: { name: 'submit_analysis', arguments: '[1,2]' } }] }),
    event({ tool_calls: [{ index: 2, type: 'function', function: { name: 'submit_analysis', arguments: '{"confidence":20}' } }] }),
    event({ tool_calls: [{ index: 1, type: 'function', function: { name: 'submit_analysis', arguments: '{"confidence":95}' } }] }, 'tool_calls'),
  ]));

  const response = await complete();
  assert.equal(response.status, 200);
  const { done: payload } = await readSse(response);
  assert.equal(payload.stopReason, 'toolUse');
  assert.deepEqual(payload.toolUse, {
    id: '',
    name: 'submit_analysis',
    input: { confidence: 95 },
  });
});

test('supports real non-streaming (stream: false) completions, null content, tool calls, and cache stats', async context => {
  const requests = [];
  const { complete } = await startServer(context, async (resource, init) => {
    const request = new Request(resource, init);
    assert.equal(request.url, 'https://gateway.invalid/v1/chat/completions');
    assert.equal(request.headers.get('authorization'), 'Bearer offline-key');
    const body = await request.json();
    requests.push(body);
    if (requests.length === 1) {
      return Response.json({
        choices: [{ message: { role: 'assistant', content: null }, finish_reason: 'stop' }],
      });
    }
    return Response.json({
      choices: [{
        message: {
          role: 'assistant',
          content: 'done',
          tool_calls: [
            { index: 2, id: 'c2', type: 'function', function: { name: 'submit_analysis', arguments: '{"confidence":40}' } },
            { index: 0, id: 'bad', type: 'function', function: { name: 'submit_analysis', arguments: '{bad' } },
            { index: 1, id: 'c1', type: 'function', function: { name: 'submit_analysis', arguments: '{"confidence":88}' } },
          ],
        },
        finish_reason: 'tool_calls',
      }],
      usage: {
        prompt_tokens: 200,
        billing_usage: { openai_usage: { prompt_tokens_details: { cached_tokens: 45 } } },
      },
    });
  });

  const nullReply = await complete({ ...requestBody, stream: false, tools: undefined, toolChoice: undefined });
  assert.equal(nullReply.status, 200);
  assert.deepEqual(await nullReply.json(), {
    content: '', thinking: '', toolUse: null, cacheStats: null, stopReason: 'stop',
  });

  const toolReply = await complete({ ...requestBody, stream: false });
  assert.equal(toolReply.status, 200);
  assert.deepEqual(await toolReply.json(), {
    content: 'done',
    thinking: '',
    toolUse: { id: 'c1', name: 'submit_analysis', input: { confidence: 88 } },
    cacheStats: { inputTokens: 200, readTokens: 45, hitTokens: 45, creationTokens: 0, missTokens: 0 },
    stopReason: 'toolUse',
  });

  assert.equal(requests[0].stream, false);
  assert.equal(requests[0].stream_options, undefined);
  assert.equal(requests[0].max_completion_tokens, 32768);
  assert.deepEqual(requests[0].messages, requestBody.messages);
  assert.equal(requests[1].tool_choice.function.name, 'submit_analysis');
});

test('non-streaming handles 429 retries, masks 401/500, rejects length truncation, and validates stream type', async context => {
  const outcomes = [
    () => Response.json({ error: { message: 'rate-limit' } }, { status: 429, headers: { 'Retry-After': '0.01' } }),
    () => Response.json({ choices: [{ message: { content: 'recovered' }, finish_reason: 'stop' }] }),
    () => Response.json({ error: { message: 'secret-auth-msg' } }, { status: 401 }),
    () => Response.json({ choices: [{ message: { content: 'cut' }, finish_reason: 'length' }] }),
  ];
  let idx = 0;
  const { complete } = await startServer(context, async () => outcomes[idx++]());

  const badType = await complete({ ...requestBody, stream: 'false' });
  assert.equal(badType.status, 400);

  const retried = await complete({ ...requestBody, stream: false, maxRetries: 1 });
  assert.equal(retried.status, 200);
  assert.equal((await retried.json()).content, 'recovered');

  const masked401 = await complete({ ...requestBody, stream: false });
  assert.equal(masked401.status, 502);
  assert.doesNotMatch(await masked401.text(), /secret-auth-msg/);

  const truncated = await complete({ ...requestBody, stream: false });
  assert.equal(truncated.status, 502);
  assert.doesNotMatch(await truncated.text(), /cut/);
});

test('captures think, thinking, reasoning_content, reasoning_details, and chunk-split inline <think> tags without duplicating', async context => {
  const outcomes = [
    () => sse([
      event({ think: 't1 ' }),
      event({ thinking: 't2 ' }),
      event({ reasoning_content: 'rc ', reasoning_details: [{ type: 'reasoning.text', text: 'rc ' }] }),
      event({ reasoning: 'r ', reasoning_details: [{ type: 'reasoning.summary', summary: 'r ' }] }),
      event({ reasoning_details: [{ type: 'reasoning.text', text: 'rd' }] }),
      event({ content: 'visible' }, 'stop'),
    ]),
    () => sse([
      event({ content: 'pre ' }),
      event({ content: '<THI' }),
      event({ content: 'NK>inner plan</th' }),
      event({ content: 'ink>post' }, 'stop'),
    ]),
    () => Response.json({
      choices: [{
        message: { role: 'assistant', think: 'sync-plan ', content: '<THINK>hidden</think>final answer' },
        finish_reason: 'stop',
      }],
    }),
  ];
  let idx = 0;
  const { complete } = await startServer(context, async () => outcomes[idx++]());

  const res1 = await complete({ ...requestBody, tools: undefined, toolChoice: undefined });
  assert.equal(res1.status, 200);
  const sse1 = await readSse(res1);
  assert.equal(sse1.done.thinking, 't1 t2 rc r rd');
  assert.equal(sse1.done.content, 'visible');
  assert.equal(sse1.events.filter(e => e.event === 'thinking').map(e => e.data.delta).join(''), 't1 t2 rc r rd');

  const res2 = await complete({ ...requestBody, tools: undefined, toolChoice: undefined });
  assert.equal(res2.status, 200);
  const sse2 = await readSse(res2);
  assert.equal(sse2.done.thinking, 'inner plan');
  assert.equal(sse2.done.content, 'pre post');
  assert.equal(sse2.events.filter(e => e.event === 'text').map(e => e.data.delta).join(''), 'pre post');
  assert.doesNotMatch(sse2.done.content, /inner|<THINK|<\/think/i);

  const res3 = await complete({ ...requestBody, stream: false, tools: undefined, toolChoice: undefined });
  assert.equal(res3.status, 200);
  const json3 = await res3.json();
  assert.equal(json3.thinking, 'sync-plan hidden');
  assert.equal(json3.content, 'final answer');
});

test('allows slow thinking longer than idle timeoutMs while upstream stays active and emits pings', async context => {
  const encoder = new TextEncoder();
  const { complete } = await startServer(context, async () => {
    const stream = new ReadableStream({
      async start(controller) {
        for (const chunk of ['step1 ', 'step2 ', 'step3 ', 'step4']) {
          controller.enqueue(encoder.encode(
            `data: ${JSON.stringify({ id: 'fake', ...event({ reasoning_content: chunk }) })}\n\n`,
          ));
          await new Promise(resolve => setTimeout(resolve, 65));
        }
        controller.enqueue(encoder.encode(
          `data: ${JSON.stringify({ id: 'fake', ...event({ content: 'final answer' }, 'stop') })}\n\n`
          + 'data: [DONE]\n\n',
        ));
        controller.close();
      },
    });
    return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
  }, { maxTimeoutMs: 500, keepaliveMs: 25, maxStreamDurationMs: 5000 });

  const response = await complete({ ...requestBody, timeoutMs: 120, tools: undefined, toolChoice: undefined });
  assert.equal(response.status, 200);
  const { events, done } = await readSse(response);
  assert.ok(events.some(e => e.event === 'ping'));
  assert.equal(done.thinking, 'step1 step2 step3 step4');
  assert.equal(done.content, 'final answer');
});

test('aborts mid-stream with deadline_exceeded when upstream stalls silently despite pings', async context => {
  const encoder = new TextEncoder();
  const upstreamAborted = Promise.withResolvers();
  const { complete } = await startServer(context, async (_resource, init) => {
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          `data: ${JSON.stringify({ id: 'fake', ...event({ reasoning_content: 'start ' }) })}\n\n`,
        ));
        init.signal?.addEventListener('abort', () => {
          upstreamAborted.resolve();
          try {
            controller.close();
          } catch {
            // Stream may already be closed.
          }
        }, { once: true });
      },
    });
    return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
  }, { maxTimeoutMs: 500, keepaliveMs: 20, maxStreamDurationMs: 5000 });

  const response = await complete({ ...requestBody, timeoutMs: 90, tools: undefined, toolChoice: undefined });
  assert.equal(response.status, 200);
  const { events, done } = await readSse(response);
  assert.equal(done, null);
  assert.ok(events.some(e => e.event === 'ping'));
  const errEvent = events.findLast(e => e.event === 'error');
  assert.deepEqual(errEvent?.data, { error: 'deadline_exceeded' });
  await Promise.race([
    upstreamAborted.promise,
    once(AbortSignal.timeout(2000), 'abort').then(() => assert.fail('upstream did not abort')),
  ]);
});

test('masks truncated tool call SSE streams and never exposes partial tool arguments', async context => {
  const encoder = new TextEncoder();
  const { complete } = await startServer(context, async () => {
    const stream = new ReadableStream({
      async start(controller) {
        controller.enqueue(encoder.encode(
          `data: ${JSON.stringify({
            id: 'fake',
            ...event({ tool_calls: [call(0, 'call_secret', '{"secret_arg":123')] }),
          })}\n\n`,
        ));
        await new Promise(resolve => setTimeout(resolve, 15));
        controller.error(new Error('upstream-connection-reset-secret'));
      },
    });
    return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
  });

  const response = await complete();
  assert.equal(response.status, 200);
  const { events, done } = await readSse(response);
  assert.equal(done, null);
  assert.ok(events.some(e => e.event === 'tool_call' && e.data.active === true));
  const errEvent = events.findLast(e => e.event === 'error');
  assert.equal(errEvent?.data?.error, 'upstream_completion_failed');
  assert.doesNotMatch(JSON.stringify(events), /secret_arg|upstream-connection-reset-secret/);
});

test('aborts upstream mid-stream when SSE client disconnects', async context => {
  const encoder = new TextEncoder();
  const upstreamChunkSent = Promise.withResolvers();
  const disconnectObserved = Promise.withResolvers();
  const { complete } = await startServer(context, async (_resource, init) => {
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          `data: ${JSON.stringify({ id: 'fake', ...event({ reasoning_content: 'chunk-1' }) })}\n\n`,
        ));
        upstreamChunkSent.resolve();
        init.signal?.addEventListener('abort', () => {
          disconnectObserved.resolve();
          try {
            controller.close();
          } catch {
            // Stream may already be closed.
          }
        }, { once: true });
      },
    });
    return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
  });

  const controller = new AbortController();
  const response = await complete(requestBody, controller.signal);
  assert.equal(response.status, 200);
  await upstreamChunkSent.promise;
  const reader = response.body.getReader();
  const firstRead = await reader.read();
  assert.equal(firstRead.done, false);
  controller.abort();
  await Promise.race([
    disconnectObserved.promise,
    once(AbortSignal.timeout(2000), 'abort').then(() => assert.fail('upstream did not observe SSE disconnect')),
  ]);
});
