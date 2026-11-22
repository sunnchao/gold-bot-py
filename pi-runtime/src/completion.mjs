import { openAICompletionsApi } from '@earendil-works/pi-ai/api/openai-completions.lazy';
import { createModels, createProvider } from '@earendil-works/pi-ai/models';
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai/utils/event-stream';
import { retryProviderRequest } from '@earendil-works/pi-ai/utils/provider-retry';
import { getCurrentSystemPrompt, getCurrentTools } from '@earendil-works/pi-ai/utils/transcript';
import { createThinkTagSplitter, extractThinkingFromRecord, splitThinkTagsSync } from './thinking.mjs';

function tokenCount(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function messageText(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.filter(block => block?.type === 'text' && typeof block.text === 'string').map(b => b.text).join('');
  }
  return '';
}

function mapSyncFinishReason(reason, hasToolCalls) {
  if (reason === 'length') return { stopReason: 'length', isError: false };
  if (reason === 'stop' || reason === 'tool_calls' || reason === null || reason === undefined) {
    return { stopReason: hasToolCalls ? 'toolUse' : 'stop', isError: false };
  }
  return { stopReason: 'error', isError: true, errorMessage: `Provider finish_reason: ${reason}` };
}

function openAICompletionsSyncApi() {
  const stream = (model, context, options) => {
    const eventStream = createAssistantMessageEventStream();
    (async () => {
      const output = {
        role: 'assistant',
        content: [],
        api: model.api,
        provider: model.provider,
        model: model.id,
        usage: {
          input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
        },
        stopReason: 'pending',
        timestamp: Date.now(),
      };
      try {
        const messages = [];
        const systemText = getCurrentSystemPrompt(context.messages ?? []);
        if (systemText) {
          messages.push({ role: 'system', content: systemText });
        }
        for (const msg of context.messages ?? []) {
          if (msg.role === 'system') continue;
          messages.push({ role: msg.role, content: messageText(msg.content) });
        }
        const body = {
          ...(options?.samplingParams ?? {}),
          model: model.id,
          messages,
          stream: false,
          ...(options?.temperature !== undefined ? { temperature: options.temperature } : {}),
          ...(options?.maxTokens !== undefined ? { max_completion_tokens: options.maxTokens } : {}),
        };
        const declaredTools = getCurrentTools(context.messages ?? []);
        if (declaredTools.length > 0) {
          body.tools = declaredTools.map(tool => ({
            type: 'function',
            function: { name: tool.name, description: tool.description ?? '', parameters: tool.parameters },
          }));
          if (options?.toolChoice !== undefined) {
            body.tool_choice = options.toolChoice;
          }
        }

        const fetchFn = options?.fetch ?? globalThis.fetch;
        const json = await retryProviderRequest(async () => {
          const response = await fetchFn(`${model.baseUrl.replace(/\/$/, '')}/chat/completions`, {
            method: 'POST',
            headers: {
              'content-type': 'application/json',
              authorization: `Bearer ${options?.apiKey ?? ''}`,
            },
            body: JSON.stringify(body),
            signal: options?.signal,
          });
          if (!response.ok) {
            await response.body?.cancel().catch(() => {});
            const err = new Error(`HTTP ${response.status}`);
            err.status = response.status;
            err.headers = response.headers;
            throw err;
          }
          return response.json();
        }, { maxRetries: options?.maxRetries ?? 0, signal: options?.signal });

        await options?.onProviderStreamEvent?.(json, model);
        const choice = json?.choices?.[0];
        if (!isPlainObject(choice)) {
          throw new Error('Missing choice in completion response');
        }
        const message = isPlainObject(choice.message) ? choice.message : {};
        const recordThinking = extractThinkingFromRecord(message, { skipNative: false });
        const splitContent = typeof message.content === 'string' && message.content.length > 0
          ? splitThinkTagsSync(message.content)
          : { thinking: '', text: '' };
        const combinedThinking = recordThinking + splitContent.thinking;
        if (combinedThinking.length > 0) {
          output.content.push({ type: 'thinking', thinking: combinedThinking });
        }
        if (splitContent.text.length > 0) {
          output.content.push({ type: 'text', text: splitContent.text });
        }
        if (Array.isArray(message.tool_calls)) {
          for (const tool of message.tool_calls) {
            const name = tool?.function?.name ?? '';
            const rawArgs = tool?.function?.arguments;
            let parsedArgs = {};
            let argumentsError;
            try {
              const candidate = typeof rawArgs === 'string' ? JSON.parse(rawArgs) : rawArgs;
              if (isPlainObject(candidate)) {
                parsedArgs = candidate;
              } else {
                argumentsError = 'invalid_arguments';
              }
            } catch {
              argumentsError = 'invalid_json';
            }
            output.content.push({
              type: 'toolCall',
              id: typeof tool?.id === 'string' ? tool.id : '',
              name,
              arguments: parsedArgs,
              ...(argumentsError ? { argumentsError } : {}),
            });
          }
        }
        const hasToolCalls = output.content.some(block => block.type === 'toolCall');
        const mapped = mapSyncFinishReason(choice.finish_reason, hasToolCalls);
        output.stopReason = mapped.stopReason;
        if (mapped.isError) {
          output.errorMessage = mapped.errorMessage;
          eventStream.push({ type: 'error', reason: 'error', error: output });
        } else {
          eventStream.push({ type: 'done', reason: output.stopReason, message: output });
        }
        eventStream.end();
      } catch (error) {
        output.stopReason = options?.signal?.aborted ? 'aborted' : 'error';
        output.errorMessage = error instanceof Error ? error.message : String(error);
        eventStream.push({ type: 'error', reason: output.stopReason, error: output });
        eventStream.end();
      }
    })();
    return eventStream;
  };
  return { stream, streamSimple: stream };
}

function cacheStats(usage) {
  if (!usage) return null;
  const nested = usage.billing_usage?.openai_usage;
  const inputTokens = tokenCount(usage.prompt_tokens) ?? tokenCount(usage.input_tokens) ?? tokenCount(nested?.prompt_tokens);
  if (inputTokens === undefined) return null;
  const hitTokens = tokenCount(usage.prompt_cache_hit_tokens)
    ?? tokenCount(usage.prompt_tokens_details?.cached_tokens)
    ?? tokenCount(nested?.prompt_tokens_details?.cached_tokens)
    ?? tokenCount(usage.cached_tokens);
  const readTokens = tokenCount(usage.cache_read_input_tokens) ?? hitTokens ?? 0;
  return {
    inputTokens, readTokens, hitTokens: hitTokens ?? readTokens,
    creationTokens: tokenCount(usage.cache_creation_input_tokens)
      ?? tokenCount(usage.prompt_tokens_details?.cache_write_tokens) ?? 0,
    missTokens: tokenCount(usage.prompt_cache_miss_tokens) ?? 0,
  };
}

export function createCompletion({ baseUrl, apiKey, modelIds, fetch }) {
  const providerId = 'goldbot-gateway';
  const syncProviderId = 'goldbot-gateway-sync';
  const models = createModels();
  const auth = { apiKey: { name: 'Gateway key', resolve: async () => ({ auth: { apiKey } }) } };
  const buildModel = (id, provider, api) => ({
    id, name: id, api, provider, baseUrl,
    input: ['text'], reasoning: false, contextWindow: 128000, maxTokens: 32768,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    compat: {
      maxTokensField: 'max_completion_tokens', supportsDeveloperRole: false,
      supportsStrictMode: false, supportsStore: false,
    },
  });
  const ids = [...modelIds];
  models.setProvider(createProvider({
    id: providerId,
    name: 'Gold Bot gateway',
    auth,
    api: openAICompletionsApi(),
    models: ids.map(id => buildModel(id, providerId, 'openai-completions')),
  }));
  models.setProvider(createProvider({
    id: syncProviderId,
    name: 'Gold Bot gateway (non-streaming)',
    auth,
    api: openAICompletionsSyncApi(),
    models: ids.map(id => buildModel(id, syncProviderId, 'openai-completions-sync')),
  }));

  const extractFirstToolUse = (blocks, toolIndices, toolOrderIndices) => {
    let toolUse = null;
    let firstIndex = Infinity;
    let toolOrdinal = 0;
    for (const block of blocks) {
      if (block.type !== 'toolCall') continue;
      const index = (block.id ? toolIndices.get(block.id) : undefined) ?? toolOrderIndices[toolOrdinal];
      toolOrdinal += 1;
      if (block.argumentsError) continue;
      if (block.arguments === null || typeof block.arguments !== 'object' || Array.isArray(block.arguments)) {
        continue;
      }
      if (index !== undefined && index < firstIndex) {
        firstIndex = index;
        toolUse = { id: block.id, name: block.name, input: block.arguments };
      }
    }
    return toolUse;
  };

  return async (request, signal, hooks = {}) => {
    let usage;
    const toolIndices = new Map();
    const seenIndices = new Set();
    const toolOrderIndices = [];
    const { messages, tools, toolChoice, maxTokens, temperature, timeoutMs, maxRetries, parameters, stream } = request;
    const targetProvider = stream === false ? syncProviderId : providerId;
    const model = models.getModel(targetProvider, request.model);
    const context = {
      systemPrompt: messages[0].role === 'system' ? messages[0].content : undefined,
      messages: messages.filter(message => message.role === 'user').map(message => ({ ...message, timestamp: 0 })),
      tools: tools?.map(({ function: tool }) => ({
        name: tool.name, description: tool.description, parameters: tool.parameters,
      })),
    };
    const recordFrameMetadata = frame => {
      const choice = frame?.choices?.[0];
      usage = frame?.usage ?? choice?.usage ?? usage;
      const rawToolCalls = choice?.delta?.tool_calls ?? choice?.message?.tool_calls;
      if (Array.isArray(rawToolCalls)) {
        for (let pos = 0; pos < rawToolCalls.length; pos += 1) {
          const tool = rawToolCalls[pos];
          const index = Number.isSafeInteger(tool?.index) ? tool.index : pos;
          if (!seenIndices.has(index)) {
            seenIndices.add(index);
            toolOrderIndices.push(index);
          }
          if (typeof tool?.id === 'string' && tool.id) {
            toolIndices.set(tool.id, index);
          }
        }
      }
      return choice;
    };

    if (stream === false) {
      const result = await models.complete(model, context, {
        fetch, signal, toolChoice, maxTokens, temperature, timeoutMs, maxRetries,
        cacheRetention: 'none', samplingParams: parameters,
        onProviderStreamEvent: frame => {
          recordFrameMetadata(frame);
        },
      });

      if (result.stopReason !== 'stop' && result.stopReason !== 'toolUse') {
        return { stopReason: result.stopReason };
      }

      return {
        content: result.content
          .filter(block => block.type === 'text' && typeof block.text === 'string')
          .map(block => block.text)
          .join(''),
        thinking: result.content
          .filter(block => block.type === 'thinking' && typeof block.thinking === 'string')
          .map(block => block.thinking)
          .join(''),
        toolUse: extractFirstToolUse(result.content, toolIndices, toolOrderIndices),
        cacheStats: cacheStats(usage),
        stopReason: result.stopReason,
      };
    }

    let streamStarted = false;
    const ensureStreamStarted = () => {
      if (streamStarted) return;
      streamStarted = true;
      hooks.onStreamStart?.();
    };

    const thinkingParts = [];
    const textParts = [];
    let seenProviderFrame = false;
    let seenThinkingDelta = false;
    let seenTextDelta = false;
    let seenToolHeartbeat = false;
    let lastToolHeartbeatAt = 0;

    const emitToolHeartbeat = () => {
      ensureStreamStarted();
      const now = Date.now();
      if (!seenToolHeartbeat || now - lastToolHeartbeatAt >= 500) {
        seenToolHeartbeat = true;
        lastToolHeartbeatAt = now;
        hooks.onEvent?.({ type: 'tool_call', active: true });
      }
    };

    const thinkTagSplitter = createThinkTagSplitter({
      onThinking: delta => {
        ensureStreamStarted();
        thinkingParts.push(delta);
        hooks.onEvent?.({ type: 'thinking', delta });
      },
      onText: delta => {
        ensureStreamStarted();
        textParts.push(delta);
        hooks.onEvent?.({ type: 'text', delta });
      },
    });

    const eventStream = models.stream(model, context, {
      fetch, signal, toolChoice, maxTokens, temperature, timeoutMs, maxRetries,
      cacheRetention: 'none', samplingParams: parameters,
      onResponse: () => {
        hooks.onActivity?.();
      },
      onProviderStreamEvent: frame => {
        hooks.onActivity?.();
        seenProviderFrame = true;
        const choice = recordFrameMetadata(frame);
        const delta = choice?.delta;
        if (!delta || typeof delta !== 'object') return;

        const frameThinking = extractThinkingFromRecord(delta, { skipNative: false });
        if (frameThinking) {
          ensureStreamStarted();
          thinkingParts.push(frameThinking);
          hooks.onEvent?.({ type: 'thinking', delta: frameThinking });
        }
        if (typeof delta.content === 'string' && delta.content.length > 0) {
          ensureStreamStarted();
          thinkTagSplitter.push(delta.content);
        }
        if (Array.isArray(delta.tool_calls) && delta.tool_calls.length > 0) {
          emitToolHeartbeat();
        }
      },
    });

    let finalResult;
    for await (const event of eventStream) {
      hooks.onActivity?.();
      if (event.type === 'start') {
        ensureStreamStarted();
      } else if (event.type === 'thinking_delta') {
        if (!seenProviderFrame && typeof event.delta === 'string' && event.delta.length > 0) {
          ensureStreamStarted();
          seenThinkingDelta = true;
          thinkingParts.push(event.delta);
          hooks.onEvent?.({ type: 'thinking', delta: event.delta });
        }
      } else if (event.type === 'text_delta') {
        if (!seenProviderFrame && typeof event.delta === 'string' && event.delta.length > 0) {
          ensureStreamStarted();
          seenTextDelta = true;
          thinkTagSplitter.push(event.delta);
        }
      } else if (event.type === 'toolcall_start' || event.type === 'toolcall_delta') {
        if (!seenProviderFrame) emitToolHeartbeat();
      } else if (event.type === 'done') {
        thinkTagSplitter.flush();
        finalResult = event.message;
      } else if (event.type === 'error') {
        thinkTagSplitter.flush();
        finalResult = event.error;
      }
    }
    thinkTagSplitter.flush();

    if (!finalResult) {
      return { stopReason: signal?.aborted ? 'aborted' : 'error' };
    }
    if (finalResult.stopReason !== 'stop' && finalResult.stopReason !== 'toolUse') {
      return { stopReason: finalResult.stopReason };
    }

    if (!seenProviderFrame && !seenThinkingDelta && thinkingParts.length === 0) {
      for (const block of finalResult.content) {
        if (block.type === 'thinking' && typeof block.thinking === 'string' && block.thinking.length > 0) {
          thinkingParts.push(block.thinking);
        }
      }
    }
    if (!seenProviderFrame && !seenTextDelta) {
      for (const block of finalResult.content) {
        if (block.type !== 'text' || typeof block.text !== 'string' || block.text.length === 0) continue;
        const split = splitThinkTagsSync(block.text);
        if (split.thinking) thinkingParts.push(split.thinking);
        if (split.text) textParts.push(split.text);
      }
    }

    return {
      content: textParts.join(''),
      thinking: thinkingParts.join(''),
      toolUse: extractFirstToolUse(finalResult.content, toolIndices, toolOrderIndices),
      cacheStats: cacheStats(usage),
      stopReason: finalResult.stopReason,
    };
  };
}
