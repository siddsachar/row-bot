/**
 * Which TypeErrors mean "the connection was lost".
 *
 * `fetch` and stream reads reject with a TypeError when a request or response
 * is lost, and the client says "Disconnected" and reconnects. The JavaScript
 * engine throws TypeErrors for bugs too ("x is not a function"); reporting
 * those as a lost connection hides them behind a Reconnect that cannot help.
 * So only the transport's failures, and the controller's own offline signals,
 * count as network failures.
 */
const failures = new WeakSet<object>();

/** A TypeError that says the connection is unavailable. */
export function networkFailure(message = 'Network unavailable'): TypeError {
  const error = new TypeError(message);
  failures.add(error);
  return error;
}

/** Record that `error` came from the network, when it is a TypeError. */
export function markNetworkFailure<T>(error: T): T {
  if (error instanceof TypeError) failures.add(error);
  return error;
}

export function isNetworkFailure(value: unknown): boolean {
  return value instanceof TypeError && failures.has(value);
}

async function* reportingStream<T>(
  source: AsyncIterable<T>,
): AsyncGenerator<T> {
  try {
    yield* source;
  } catch (error) {
    throw markNetworkFailure(error);
  }
}

/**
 * The transport as the controller sees it: every TypeError it throws, rejects
 * with or raises while streaming events is a network failure.
 */
export function reportNetworkFailures<T extends object>(transport: T): T {
  return new Proxy(transport, {
    get(target, property, receiver) {
      const value: unknown = Reflect.get(target, property, receiver);
      if (typeof value !== 'function') return value;
      return (...args: unknown[]) => {
        let result: unknown;
        try {
          result = value.apply(target, args);
        } catch (error) {
          throw markNetworkFailure(error);
        }
        if (
          result &&
          typeof (result as PromiseLike<unknown>).then === 'function'
        )
          return Promise.resolve(result).catch((error: unknown) => {
            throw markNetworkFailure(error);
          });
        if (
          result &&
          typeof (result as AsyncIterable<unknown>)[Symbol.asyncIterator] ===
            'function'
        )
          return reportingStream(result as AsyncIterable<unknown>);
        return result;
      };
    },
  });
}
