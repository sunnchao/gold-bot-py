export const PI_AI_NATIVE_REASONING_FIELDS = ['reasoning_content', 'reasoning', 'reasoning_text'];
export const EXTRA_REASONING_FIELDS = ['think', 'thinking'];
export const ALL_REASONING_FIELDS = [...PI_AI_NATIVE_REASONING_FIELDS, ...EXTRA_REASONING_FIELDS];

const OPEN_THINK_TAG = '<think>';
const CLOSE_THINK_TAG = '</think>';

function isPlainRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Extract plain reasoning text from OpenAI/OpenRouter-style `reasoning_details`
 * payloads (array of detail objects or a single detail object).
 */
export function extractReasoningDetailsText(details) {
  const items = Array.isArray(details) ? details : isPlainRecord(details) ? [details] : [];
  if (items.length === 0) return '';

  const parts = [];
  for (const item of items) {
    if (!isPlainRecord(item)) continue;
    if (typeof item.text === 'string' && item.text.length > 0) {
      parts.push(item.text);
    } else if (typeof item.summary === 'string' && item.summary.length > 0) {
      parts.push(item.summary);
    } else if (typeof item.thinking === 'string' && item.thinking.length > 0) {
      parts.push(item.thinking);
    }
  }
  return parts.join('');
}

/**
 * Extract thinking text from a delta or message record.
 * When `skipNative` is true, native pi-ai fields (`reasoning_content`,
 * `reasoning`, `reasoning_text`) are skipped so `pi-ai`'s `thinking_delta`
 * events are not duplicated, and `reasoning_details` is skipped when a native
 * field already exists on the same record.
 */
export function extractThinkingFromRecord(record, { skipNative = false } = {}) {
  if (!isPlainRecord(record)) return '';

  const nativePresent = PI_AI_NATIVE_REASONING_FIELDS.some(
    field => typeof record[field] === 'string' && record[field].length > 0,
  );
  const fields = skipNative ? EXTRA_REASONING_FIELDS : ALL_REASONING_FIELDS;

  for (const field of fields) {
    if (typeof record[field] === 'string' && record[field].length > 0) {
      return record[field];
    }
  }

  if (skipNative && nativePresent) {
    return '';
  }

  return extractReasoningDetailsText(record.reasoning_details);
}

function trailingTagPrefixLength(text, tag) {
  const lower = text.toLowerCase();
  const maxPrefix = Math.min(lower.length, tag.length - 1);
  for (let len = maxPrefix; len > 0; len -= 1) {
    if (tag.startsWith(lower.slice(lower.length - len))) {
      return len;
    }
  }
  return 0;
}

/**
 * Stateful streaming splitter that separates inline `<think>...</think>` blocks
 * from visible text, even when tags are split across chunks or uppercase.
 */
export function createThinkTagSplitter({ onThinking, onText } = {}) {
  let inThink = false;
  let carry = '';

  const emit = (chunk, thinking) => {
    if (!chunk) return;
    if (thinking) {
      onThinking?.(chunk);
    } else {
      onText?.(chunk);
    }
  };

  return {
    push(chunk) {
      if (typeof chunk !== 'string' || chunk.length === 0) return;
      let buffer = carry + chunk;
      carry = '';

      while (buffer.length > 0) {
        const tag = inThink ? CLOSE_THINK_TAG : OPEN_THINK_TAG;
        const idx = buffer.toLowerCase().indexOf(tag);
        if (idx !== -1) {
          emit(buffer.slice(0, idx), inThink);
          inThink = !inThink;
          buffer = buffer.slice(idx + tag.length);
          continue;
        }

        const keep = trailingTagPrefixLength(buffer, tag);
        if (keep > 0) {
          emit(buffer.slice(0, buffer.length - keep), inThink);
          carry = buffer.slice(buffer.length - keep);
        } else {
          emit(buffer, inThink);
        }
        break;
      }
    },

    flush() {
      if (!carry) return;
      emit(carry, inThink);
      carry = '';
    },
  };
}

/**
 * Synchronous helper to split inline `<think>...</think>` blocks from text.
 */
export function splitThinkTagsSync(rawText) {
  if (typeof rawText !== 'string' || rawText.length === 0) {
    return { thinking: '', text: '' };
  }
  const thinkingParts = [];
  const textParts = [];
  const splitter = createThinkTagSplitter({
    onThinking: delta => thinkingParts.push(delta),
    onText: delta => textParts.push(delta),
  });
  splitter.push(rawText);
  splitter.flush();
  return {
    thinking: thinkingParts.join(''),
    text: textParts.join(''),
  };
}
