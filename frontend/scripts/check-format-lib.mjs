/** Parse a newline-delimited, sorted formatting-debt baseline. */
export function parseDebtBaseline(source) {
  const entries = source
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter((line) => line.length > 0 && !line.startsWith('#'));
  const sorted = [...entries].sort();
  const unique = [...new Set(entries)];

  if (unique.length !== entries.length) {
    throw new Error('format debt baseline contains duplicate paths');
  }
  if (entries.some((entry, index) => entry !== sorted[index])) {
    throw new Error('format debt baseline must be sorted');
  }
  if (entries.some((entry) => entry.startsWith('/') || entry.includes('\\'))) {
    throw new Error('format debt baseline paths must be relative POSIX paths');
  }

  return new Set(entries);
}

/** Normalize one-path-per-line command output into a sorted unique list. */
export function parsePathLines(source) {
  return [
    ...new Set(
      source
        .split(/\r?\n/u)
        .map((line) => line.trim().replaceAll('\\', '/'))
        .filter(Boolean),
    ),
  ].sort();
}

/**
 * Compare the current full-tree drift with the accepted historical baseline.
 * Cleared paths are intentionally allowed; new paths are always violations.
 */
export function evaluateDebt({ baseline, currentDrift }) {
  const current = new Set(currentDrift);
  return {
    newDebt: [...current].filter((path) => !baseline.has(path)).sort(),
    clearedDebt: [...baseline].filter((path) => !current.has(path)).sort(),
  };
}

/** A clean gate has no new debt, stale baseline entries, or touched-file drift. */
export function hasFormatViolations({ newDebt, clearedDebt, touchedDrift }) {
  return newDebt.length > 0 || clearedDebt.length > 0 || touchedDrift.length > 0;
}

/** Keep only repository-relative paths that are safe to pass as argv entries. */
export function normalizeChangedPaths(paths) {
  return [
    ...new Set(
      paths
        .map((path) => path.trim().replaceAll('\\', '/'))
        .filter(
          (path) =>
            path.length > 0 &&
            !path.startsWith('/') &&
            path !== '..' &&
            !path.startsWith('../') &&
            !path.includes('\0'),
        ),
    ),
  ].sort();
}

/** Limit this gate to the frontend tree owned by the frontend workflow. */
export function frontendChangedPaths(paths) {
  return normalizeChangedPaths(paths).filter((path) => path.startsWith('frontend/'));
}
