/**
 * Season Utilities - Dynamic Season Minus Logic & Year Calculations
 * 
 * Supports long-term automatic season operations:
 *   AW-26 -> AW-25
 *   SS-26 -> SS-25
 *   AW-27 -> AW-26
 *   SS-27 -> SS-26
 *   AW26  -> AW25
 *   SS 26 -> SS 25
 */

/**
 * Returns the previous year's season string.
 *
 * Examples:
 *   getPreviousSeason('AW-26')  => 'AW-25'
 *   getPreviousSeason('SS-26')  => 'SS-25'
 *   getPreviousSeason('AW-27')  => 'AW-26'
 *   getPreviousSeason('SS-27')  => 'SS-26'
 *   getPreviousSeason('AW26')   => 'AW25'
 *   getPreviousSeason('AW-2026') => 'AW-2025'
 */
export function getPreviousSeason(season: string): string {
  if (!season || typeof season !== 'string') return '';
  const s = season.trim();
  if (!s) return '';

  const match = s.match(/^([A-Za-z\s]+)([\-_]*\s*)(\d{2,4})$/);
  if (match) {
    const [, prefix, sep, yrStr] = match;
    const yr = parseInt(yrStr, 10);
    const prevYr = yr - 1;
    const prevYrStr = String(prevYr).padStart(yrStr.length, '0');
    return `${prefix}${sep}${prevYrStr}`.toUpperCase();
  }

  return s.toUpperCase();
}

export type SeasonInfo = {
  raw: string;
  prefix: string;
  year: number;
  previousSeason: string;
  normalized: string;
};

/**
 * Parses season details including 4-digit year, prefix, and previous season.
 */
export function parseSeasonInfo(season: string): SeasonInfo {
  if (!season || typeof season !== 'string') {
    return { raw: '', prefix: '', year: 0, previousSeason: '', normalized: '' };
  }

  const s = season.trim().toUpperCase();
  const match = s.match(/^([A-Za-z\s]+)([\-_]*\s*)(\d{2,4})$/);

  if (match) {
    const [, prefix, , yrStr] = match;
    const yr = parseInt(yrStr, 10);
    const fullYear = yrStr.length === 2 ? 2000 + yr : yr;
    const prev = getPreviousSeason(s);
    return {
      raw: season,
      prefix: prefix.trim(),
      year: fullYear,
      previousSeason: prev,
      normalized: `${prefix.trim()}-${yrStr}`,
    };
  }

  return {
    raw: season,
    prefix: s,
    year: 0,
    previousSeason: s,
    normalized: s,
  };
}
