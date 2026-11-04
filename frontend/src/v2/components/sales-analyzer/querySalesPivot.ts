/** Retry a transient hosted-server failure a few times; never treat a failure as empty data. */
export async function querySalesPivot(url: string, payload: unknown, signal: AbortSignal) {
  let lastDetail = '';
  let lastStatus = 0;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    signal.throwIfAborted();
    let response: Response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal,
      });
    } catch (error) {
      if (signal.aborted || attempt >= 3) throw error;
      await new Promise((resolve) => setTimeout(resolve, 2000 * (attempt + 1)));
      continue;
    }
    const data = await response.json().catch(() => null);
    signal.throwIfAborted();
    const detail = typeof data?.detail === 'string' ? data.detail : data?.error;
    lastDetail = typeof detail === 'string' ? detail : '';
    lastStatus = response.status;
    const warming =
      typeof detail === 'string' && /cache is warming/i.test(detail);
    if (attempt < 3 && ([502, 503, 504].includes(response.status) || warming)) {
      // Prepared-frame miss: short polls, not a 10s→40s wait that ends in
      // "retry in a minute" while the hierarchy query is still running.
      const waitMs = warming ? 4000 : 2000 * (attempt + 1);
      await new Promise((resolve) => setTimeout(resolve, waitMs));
      continue;
    }
    if (!response.ok || !data?.ok || !Array.isArray(data.rows)) {
      if (warming) {
        throw new Error('This hierarchy is still loading. Please wait a few seconds and try again.');
      }
      throw new Error(detail || `Sales pivot could not load (HTTP ${response.status}). Please retry.`);
    }
    return data;
  }
  if (lastDetail && /cache is warming/i.test(lastDetail)) {
    throw new Error('This hierarchy is still loading. Please wait a few seconds and try again.');
  }
  throw new Error(
    lastDetail ||
      `Sales pivot could not load (HTTP ${lastStatus || 'timeout'}). Please retry.`
  );
}
